/* Real browser geometry regression; all authentication POSTs are mocked. */
/* eslint-disable @typescript-eslint/no-require-imports -- Standalone CommonJS CLI with an optional external Playwright module. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const base = option('--base-url', 'http://127.0.0.1:3000');
const output = path.resolve(option('--output', 'test-results/captcha-layout.json'));
const strict = args.includes('--expect-stable');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const report = { scope: 'Browser geometry; mocked captcha and mail; no production writes', base, cases: [], pageErrors: [] };
fs.mkdirSync(path.dirname(output), {recursive: true});
let browser;
async function geometry(page) {
  return page.evaluate(() => {
    const selectors = { form: '.auth-form', note: '.auth-note', captcha: '.captcha-image', emailRow: '.verification-control', submit: '.form-submit' };
    return Object.fromEntries(Object.entries(selectors).map(([key, selector]) => {
      const r = document.querySelector(selector).getBoundingClientRect();
      return [key, { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height }];
    }));
  });
}
(async () => {
  browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {}), args:['--no-proxy-server'] });
  for (const width of [1440, 390]) for (const mode of ['register', 'reset']) {
    const context = await browser.newContext({viewport:{width,height:1000}, reducedMotion:'reduce'});
    let captchaCalls=0;
    await context.route('**/api/**', async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/api/auth/me') return route.fulfill({status:401,json:{error:'authentication required'}});
      if (pathname === '/api/auth/captcha') {
        captchaCalls++;
        await delay(500);
        if (captchaCalls === 3) return route.fulfill({status:503,json:{error:'验证码服务暂不可用，请稍后重试。'}});
        const svg='<svg xmlns="http://www.w3.org/2000/svg" width="180" height="56"><rect width="180" height="56" fill="#f4f6ef"/><text x="18" y="42" font-size="36">123456</text></svg>';
        return route.fulfill({status:200,json:{id:'test-'+captchaCalls,image:'data:image/svg+xml;base64,'+Buffer.from(svg).toString('base64')}});
      }
      if (pathname === '/api/auth/verification-codes') {
        await delay(500);
        return route.fulfill({status:202,json:{retry_after:60}});
      }
      return route.fulfill({status:503,json:{error:'Browser test blocks unconfigured API requests'}});
    });
    const page = await context.newPage();
    page.on('pageerror',error=>report.pageErrors.push(error.message));
    await page.goto(base+'/login?mode='+mode,{waitUntil:'domcontentloaded',timeout:60000});
    await page.waitForFunction(()=>window.__VINEXT_HYDRATED_AT && !document.querySelector('.account-loading'));
    await page.getByLabel('邮箱',{exact:true}).fill('2026000000001@std.uestc.edu.cn');
    await page.locator('.captcha-image').scrollIntoViewIfNeeded();
    await page.waitForTimeout(150);
    const initial=await geometry(page);
    const states=[];
    async function capture(name) {
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const measured=await geometry(page);
      let maxDelta=0;
      for(const key of Object.keys(initial)) for(const dimension of ['x','y','width','height']) maxDelta=Math.max(maxDelta,Math.abs(measured[key][dimension]-initial[key][dimension]));
      states.push({name,maxDelta:+maxDelta.toFixed(3),geometry:measured});
    }
    for (let attempt=1;attempt<=4;attempt++) {
      await page.locator('.captcha-image').click();
      await page.waitForTimeout(100);
      await capture('captcha-loading-'+attempt);
      await page.waitForFunction(()=>!document.querySelector('.captcha-image').disabled);
      await capture(attempt===3?'captcha-error':'captcha-ready-'+attempt);
    }
    await page.getByLabel('数字验证码',{exact:true}).fill('123456');
    const send=page.getByRole('button',{name:'发送邮件验证码',exact:true});
    assert.equal(await send.isEnabled(),true);
    const captcha=page.getByAltText('6 位数字验证码，点击更换');
    const captchaSource=await captcha.getAttribute('src');
    await send.click();
    await page.waitForTimeout(100);
    await capture('mail-loading');
    await page.getByRole('button',{name:/秒后重发/}).waitFor();
    await capture('mail-sent');
    assert.equal(await captcha.isVisible(),true);
    assert.equal(await captcha.getAttribute('src'),captchaSource);
    assert.equal(await page.getByLabel('数字验证码',{exact:true}).inputValue(),'123456');
    assert.equal(await page.getByLabel('数字验证码',{exact:true}).isDisabled(),true);
    assert.equal(await page.getByLabel('邮箱验证码',{exact:true}).isEnabled(),true);
    const maxDelta=Math.max(...states.map(s=>s.maxDelta));
    const filename=path.basename(output,'.json')+'-'+mode+'-'+width+'.png';
    await page.screenshot({path:path.join(path.dirname(output),filename),fullPage:true});
    report.cases.push({width,mode,initial,states,maxDelta,screenshot:filename});
    await context.close();
  }
  assert.equal(report.pageErrors.length,0,JSON.stringify(report.pageErrors));
  report.maxDelta=Math.max(...report.cases.map(c=>c.maxDelta));
  report.passed=report.maxDelta<=1;
  fs.writeFileSync(output,JSON.stringify(report,null,2));
  console.log(JSON.stringify({cases:report.cases.map(c=>({width:c.width,mode:c.mode,maxDelta:c.maxDelta})),maxDelta:report.maxDelta,passed:report.passed}));
  if(strict) assert.ok(report.passed,'Layout shifted by more than one CSS pixel');
})().catch(error=>{ report.failure=error.stack;fs.writeFileSync(output,JSON.stringify(report,null,2));console.error(error);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();});
