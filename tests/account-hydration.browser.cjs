/* Account inputs remain disabled until their React handlers can accept typing. */
/* eslint-disable @typescript-eslint/no-require-imports -- Optional browser regression CLI. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.env.TEST_BASE_URL||'http://127.0.0.1:30128';
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
  const results=[];
  try {
    for(const width of [1440,390]) {
      const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'});
      let releaseScripts;
      const scriptsReady=new Promise(resolve=>{releaseScripts=resolve;});
      await context.route('**/assets/*.js',async route=>{await scriptsReady;await route.continue();});
      await context.route('**/api/**',route=>route.fulfill({status:401,json:{error:'authentication required'}}));
      const page=await context.newPage();
      const errors=[];page.on('pageerror',error=>errors.push(error.message));
      await page.goto(base+'/login?mode=register',{waitUntil:'domcontentloaded'});
      const email=page.getByLabel('邮箱',{exact:true});
      assert.equal(await email.isDisabled(),true,'SSR form must not accept input without handlers');
      releaseScripts();
      await email.fill('campus.alias@std.uestc.edu.cn');
      const student=page.getByLabel('学号',{exact:true});await student.waitFor();
      await student.fill('2026123456789');
      assert.equal(await student.inputValue(),'2026123456789');
      assert.deepEqual(errors,[]);results.push({width,passed:true});await context.close();
    }
  }finally{await browser.close();}
  fs.mkdirSync('outputs',{recursive:true});fs.writeFileSync('outputs/account-hydration-browser.json',JSON.stringify({passed:true,results},null,2));
  console.log(JSON.stringify({passed:true,results}));
})().catch(error=>{console.error(error);process.exitCode=1;});
