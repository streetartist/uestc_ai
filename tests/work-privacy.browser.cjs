/* eslint-disable @typescript-eslint/no-require-imports -- Optional Playwright CLI. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.env.TEST_BASE_URL||'http://127.0.0.1:30128';
const output=path.resolve(process.env.PRIVACY_TEST_OUTPUT||'outputs/work-privacy-browser.json');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-proxy-server']});
  const cases=[];
  try {
    for(const width of [1440,390]) for(const status of [401,403,404]) {
      const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'});
      await context.addInitScript(()=>{
        const interval=window.setInterval;
        window.setInterval=(callback,ms,...args)=>interval(callback,ms===10000?250:ms,...args);
      });
      let revoked=false,reads=0;
      await context.route('**/api/**',route=>{
        const endpoint=new URL(route.request().url()).pathname;
        if(endpoint==='/api/auth/me') return route.fulfill({json:{user:{id:'owner',name:'测试用户',email:'test@example.com',role:'member'}}});
        if(endpoint==='/api/works/private') {
          reads++;
          if(revoked) return route.fulfill({status,json:{error:status===401?'authentication required':status===403?'insufficient permissions':'work not found'}});
          return route.fulfill({json:{id:'private',title:'受限作品标题',team_name:'本队',team_members:[],
            competition:{name:'赛事',slug:'test'},track:{name:'赛道',slug:'test'},problem:{code:'T',title:'赛题',slug:'test'},
            version:{created_at:'2026-10-15T00:00:00Z',readme_md:'不能继续显示的私有材料',fields:{}},assets:[],
            evaluation_config:{adapter:'minecraft-agent-v1',metrics:['score']},
            evaluation:{id:'run',status:'running',attempts:1,created_at:'2026-10-15T00:00:00Z',started_at:null,finished_at:null,time_seconds:300,artifacts:[]}}});
        }
        return route.fulfill({status:404,json:{error:'not found'}});
      });
      const page=await context.newPage(),errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.goto(base+'/works/private');
      await page.getByText('不能继续显示的私有材料',{exact:true}).waitFor();
      await page.getByRole('region',{name:'运行指标'}).waitFor();
      revoked=true;
      await page.locator('.work-layout').waitFor({state:'detached',timeout:3000});
      assert.equal(await page.getByText('不能继续显示的私有材料',{exact:true}).count(),0);
      assert.equal(await page.getByRole('heading',{name:'受限作品标题'}).count(),0);
      assert.equal(await page.getByRole('region',{name:'运行指标'}).count(),0);
      assert.ok(reads>=2); assert.deepEqual(errors,[]);
      cases.push({width,status,passed:true});
      await context.close();
    }
    fs.mkdirSync(path.dirname(output),{recursive:true});
    fs.writeFileSync(output,JSON.stringify({passed:true,scope:'Mocked API; revoked access clears materials and metrics',cases},null,2));
    console.log(JSON.stringify({passed:true,cases:cases.length,output}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
