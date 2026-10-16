/* Showcase management and public viewing with mocked account and API data. */
/* eslint-disable @typescript-eslint/no-require-imports -- Optional standalone browser regression. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:30128';
(async () => {
  const report = [];
  for (const [name, executablePath] of [['Chrome', process.env.BROWSER_EXECUTABLE], ['Edge', process.env.EDGE_EXECUTABLE]]) {
    if (!executablePath) continue;
    const browser = await chromium.launch({headless:true, executablePath, args:['--no-proxy-server']});
    try {
      for (const width of [1440,390]) {
        const context = await browser.newContext({viewport:{width,height:1000}, acceptDownloads:true, reducedMotion:'reduce'});
        let publication = {}, lastSave, lastRegistration;
        const errors=[];
        const work = {id:'showcase',title:'Original private title',team_id:'team',team_name:'Team',problem_id:'p',problem_code:'P',
          track_name:'Track',competition_id:'c',competition_name:'Competition',version_id:'v',competition_ended:true};
        await context.route('**/api/**', route => {
          const req=route.request(), url=new URL(req.url());
          if(url.pathname==='/api/auth/register') { lastRegistration=req.postDataJSON(); return route.fulfill({status:400,json:{error:'Browser test registration intercepted'}}); }
          if(url.pathname==='/api/auth/me') return route.fulfill({json:{user:{id:'admin',name:'Admin',email:'admin@example.com',role:'admin'}}});
          if(url.pathname==='/api/manage/catalog') return route.fulfill({json:[{id:'c',name:'Competition',tracks:[]}]});
          if(url.pathname==='/api/manage/submissions') return route.fulfill({json:[{...work,publication}]});
          if(url.pathname==='/api/manage/submissions/showcase/publication') {
            if(req.method()==='PATCH') {lastSave=req.postDataJSON(); publication=lastSave.published ? lastSave : {}; return route.fulfill({json:{publication}});}
            return route.fulfill({json:{version_id:'v',version_created_at:'2026-10-16T00:00:00',publication,competition_ended:true,assets:[{id:'code',original_name:'private-code.zip',size:42}]}});
          }
          if(url.pathname==='/api/works/showcase') return route.fulfill({json:{...work,view:'public',title:'Public title',team_members:[],
            competition:{name:'Competition',slug:'c'},track:{name:'Track',slug:'t'},problem:{code:'P',title:'Problem',slug:'p'},
            version:{created_at:'2026-10-16T00:00:00Z',readme_md:'Public introduction',fields:{},snapshot:{}},assets:[],public_metrics:{metrics:{task_success:75},definitions:[{key:'task_success',label:'成功率',unit:'%'}]}}});
          return route.fulfill({status:404,json:{error:'not found'}});
        });
        const page=await context.newPage(); page.on('pageerror',e=>errors.push(e.message));
        await page.goto(base+'/manage/works');
        await page.getByLabel('公开标题',{exact:true}).fill('Public title');
        await page.getByLabel('公开作品介绍（Markdown）',{exact:true}).fill('Public introduction');
        assert.equal(await page.getByRole('checkbox',{name:'private-code.zip'}).isChecked(),false);
        assert.equal(await page.getByRole('checkbox',{name:'公开详细指标'}).isChecked(),false);
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth + 1));
        await page.screenshot({path:`outputs/showcase-${name}-${width}.png`,fullPage:true});
        await page.getByRole('button',{name:'发布公开展示',exact:true}).click();
        await page.getByRole('button',{name:'更新公开展示',exact:true}).waitFor();
        assert.equal(lastSave.description_md,'Public introduction'); assert.deepEqual(lastSave.asset_ids,[]); assert.equal(lastSave.metrics_public,false);
        await page.getByRole('checkbox',{name:'private-code.zip'}).check();
        await page.getByRole('checkbox',{name:'公开详细指标'}).check();
        await page.getByRole('button',{name:'更新公开展示',exact:true}).click();
        await page.waitForFunction(()=>!document.querySelector('fieldset')?.disabled);
        assert.deepEqual(lastSave.asset_ids,['code']); assert.equal(lastSave.metrics_public,true);
        await page.getByRole('button',{name:'撤回公开展示',exact:true}).click();
        await page.getByRole('button',{name:'发布公开展示',exact:true}).waitFor(); assert.equal(lastSave.published,false);
        await page.goto(base+'/works/showcase');
        await page.getByText('Public introduction',{exact:true}).waitFor();
        assert.equal(await page.getByRole('region',{name:'公开指标'}).count(),1);
        assert.equal(await page.getByText('提交字段',{exact:true}).count(),0);
        assert.equal(await page.locator('.evaluation-logs').count(),0);
        await page.goto(base+'/login?mode=register');
        await page.getByLabel('邮箱',{exact:true}).fill('campus.alias@std.uestc.edu.cn');
        const student=page.getByLabel('学号',{exact:true}); await student.waitFor();
        assert.equal(await student.getAttribute('required'),'');
        await page.getByLabel('姓名或昵称',{exact:true}).fill('Student');
        await page.locator('.auth-form input[type=password]').first().fill('secure-pass!');
        await page.getByLabel('确认密码',{exact:true}).fill('secure-pass!');
        await page.getByLabel('邮箱验证码',{exact:true}).fill('123456');
        await page.getByRole('button',{name:'创建账户',exact:true}).click();
        assert.equal(lastRegistration,undefined);
        await student.fill('1999123456');
        const registrationResponse=page.waitForResponse(response=>response.url().endsWith('/api/auth/register'));
        await page.getByRole('button',{name:'创建账户',exact:true}).click();
        await registrationResponse;
        assert.equal(lastRegistration.student_number,'1999123456');
        assert.equal(lastRegistration.email,'campus.alias@std.uestc.edu.cn');
        await page.getByLabel('邮箱',{exact:true}).fill('outside@example.com');
        assert.equal(await page.getByLabel('学号',{exact:true}).count(),0);
        await page.getByLabel(/^注册邀请码/).waitFor();
        assert.deepEqual(errors,[]);
        report.push({browser:name,width,passed:true});
        await context.close();
      }
    } finally {await browser.close();}
  }
  fs.mkdirSync('outputs',{recursive:true}); fs.writeFileSync('outputs/showcase-browser.json',JSON.stringify({passed:true,cases:report},null,2));
  console.log(JSON.stringify(report));
})().catch(error=>{console.error(error);process.exitCode=1;});
