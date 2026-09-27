// Runs ICBC's lock -> send code -> verify -> book steps inside real (headless) Chromium.
// ICBC rejects these calls from plain HTTP clients, but accepts them from the web app's own
// browser session, so we sign in through the UI and then call the API from inside the page.
//
// Usage: node browser.js <stateDir>
//   <stateDir>/request.json  {posId, date, startTm, drvrId, lockOnly}
//   <stateDir>/status.json   written by this script; last write wins
//   <stateDir>/otp_code      written by `python3 -m icbc_booker confirm`; polled by this script
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const stateDir = process.argv[2];
const req = JSON.parse(fs.readFileSync(path.join(stateDir, 'request.json'), 'utf8'));
const codeFile = path.join(stateDir, 'otp_code');
const OTP_WAIT_MS = 12 * 60 * 1000;
const MAX_CODE_TRIES = 3;

function report(obj) {
  obj.at = new Date().toISOString();
  fs.writeFileSync(path.join(stateDir, 'status.json'), JSON.stringify(obj));
  console.log(JSON.stringify(obj));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Cloud sessions reach ICBC through an HTTPS proxy whose CA is not in Chromium's store.
// Trust exactly that CA (by its public-key hash); every other certificate is still verified.
function proxyCaArgs() {
  const caPath = process.env.ICBC_PROXY_CA || '/root/.ccr/agent-proxy-ca.crt';
  if (!process.env.HTTPS_PROXY || !fs.existsSync(caPath)) return [];
  const certs = fs.readFileSync(caPath, 'utf8').match(/-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----/g) || [];
  const hashes = certs.map((pem) => crypto.createHash('sha256')
    .update(new crypto.X509Certificate(pem).publicKey.export({ type: 'spki', format: 'der' })).digest('base64'));
  return hashes.length ? [`--ignore-certificate-errors-spki-list=${hashes.join(',')}`] : [];
}

async function api(page, method, p, body) {
  return page.evaluate(async ({ method, p, body }) => {
    const r = await fetch('https://onlinebusiness.icbc.com/deas-api/v1' + p, {
      method,
      headers: { 'Content-Type': 'application/json', Authorization: localStorage.getItem('AUTH_TOKEN') },
      body: JSON.stringify(body),
    });
    const text = await r.text();
    let json = null;
    try { json = JSON.parse(text); } catch (e) { /* not JSON */ }
    return { ok: r.ok, status: r.status, json, text: text.slice(0, 500) };
  }, { method, p, body });
}

async function signIn(page) {
  await page.goto('https://onlinebusiness.icbc.com/webdeas-ui/', { waitUntil: 'networkidle' });
  await page.getByText('Next', { exact: true }).click();
  await page.locator('input[formcontrolname="drvrLastName"]').fill(process.env.ICBC_LAST_NAME);
  await page.locator('input[formcontrolname="licenceNumber"]').fill(process.env.ICBC_LICENCE);
  await page.locator('input[formcontrolname="keyword"]').fill(process.env.ICBC_KEYWORD);
  await page.locator('mat-checkbox').click();
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForFunction(() => !!localStorage.getItem('AUTH_TOKEN'), null, { timeout: 30000 });
}

async function main() {
  const browser = await chromium.launch({
    proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined,
    args: proxyCaArgs(),
  });
  try {
    const ctx = await browser.newContext({ timezoneId: 'America/Vancouver' });
    const page = await ctx.newPage();
    await signIn(page);

    // Re-read availability in this session so the slot signature is fresh and ours.
    const avail = await api(page, 'POST', '/web/getAvailableAppointments', {
      aPosID: req.posId, examType: '6-R-1', examDate: req.date,
      prfDaysOfWeek: '[0,1,2,3,4,5,6]', prfPartsOfDay: '[0,1]',
      lastName: process.env.ICBC_LAST_NAME.toUpperCase(), licenseNumber: process.env.ICBC_LICENCE,
    });
    if (!avail.ok) return report({ status: 'ERROR', step: 'search', http: avail.status, error: avail.text });
    const slot = (avail.json || []).find((s) => s.appointmentDt.date === req.date && s.startTm === req.startTm);
    if (!slot) return report({ status: 'GONE' });

    // Same timestamp format the web app builds: local YYYY-MM-DDTHH:MM:SS.
    const bookedTs = await page.evaluate(() => {
      const t = new Date(); const p = (n) => String(n).padStart(2, '0');
      return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}T${t.toTimeString().split(' ')[0]}`;
    });
    const lock = await api(page, 'PUT', '/web/lock', {
      appointmentDt: slot.appointmentDt, dlExam: slot.dlExam, drvrDriver: { drvrId: req.drvrId },
      drscDrvSchl: {}, instructorDlNum: null, bookedTs, startTm: slot.startTm, endTm: slot.endTm,
      posId: slot.posId, resourceId: slot.resourceId, signature: slot.signature,
    });
    if (!lock.ok) return report({ status: 'LOCK_FAILED', http: lock.status, error: lock.text });
    if (req.lockOnly) return report({ status: 'LOCKED', bookedTs });

    const otp = await api(page, 'POST', '/web/sendOTP', { bookedTs, drvrID: req.drvrId, method: 'E' });
    if (!otp.ok) return report({ status: 'ERROR', step: 'sendOTP', http: otp.status, error: otp.text });
    report({ status: 'OTP_SENT', bookedTs });

    const deadline = Date.now() + OTP_WAIT_MS;
    for (let tries = 0; tries < MAX_CODE_TRIES && Date.now() < deadline;) {
      if (!fs.existsSync(codeFile)) { await sleep(1000); continue; }
      const code = fs.readFileSync(codeFile, 'utf8').trim();
      fs.unlinkSync(codeFile);
      tries += 1;
      const v = await api(page, 'PUT', '/web/verifyOTP', { bookedTs, drvrID: req.drvrId, code });
      if (!v.ok || (v.json || {}).status !== 'VERIFIED') {
        report({ status: 'OTP_REJECTED', http: v.status, response: v.json || v.text, triesLeft: MAX_CODE_TRIES - tries });
        continue;
      }
      const book = await api(page, 'PUT', '/web/book', {
        userId: `WEBD:${req.drvrId}`, appointment: { drvrDriver: { drvrId: req.drvrId } },
      });
      if (!book.ok) return report({ status: 'ERROR', step: 'book', http: book.status, error: book.text });
      return report({ status: 'BOOKED', response: book.json });
    }
    return report({ status: 'OTP_TIMEOUT' });
  } finally {
    await browser.close();
  }
}

main().catch((e) => report({ status: 'ERROR', error: String(e && e.stack || e).slice(0, 800) }));
