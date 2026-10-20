// Offline UI behavior checks. Run with: node test_ui.cjs (no npm dependencies).
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(__dirname+'/webui.html','utf8');
const catalog=JSON.parse(fs.readFileSync(__dirname+'/locales.json','utf8'));
const code=html.slice(html.lastIndexOf('<script>')+8,html.lastIndexOf('</script>')).replace('__APP_PREFERRED_LANGUAGE__','null');
class Element{
 constructor(id=''){this.id=id;this.textContent='';this.value='';this.style={};this.attrs={};this.children=[];this.dataset={};this.classList={add(){},remove(){}};this.disabled=false;this.checked=true}
 setAttribute(k,v){this.attrs[k]=v} append(...v){this.children.push(...v)} replaceChildren(...v){this.children=v} addEventListener(){} click(){} showModal(){this.open=true} close(){this.open=false}
}
const elements={};
for(const m of html.matchAll(/id="([^"]+)"/g))elements[m[1]]=new Element(m[1]);
elements.localeData.textContent=JSON.stringify(catalog);elements.quality.value='320';
const translated=[...html.matchAll(/data-i18n="([^"]+)"/g)].map(m=>{const e=new Element();e.dataset.i18n=m[1];return e});
let requests=[],saved={};
const context=vm.createContext({
 document:{getElementById:id=>elements[id],createElement:tag=>new Element(),documentElement:{},querySelectorAll:()=>translated},
 navigator:{languages:['fr-FR','ja-JP']},localStorage:{getItem:k=>saved[k],setItem:(k,v)=>{saved[k]=v}},
 fetch:async(path,options)=>{requests.push({path,options});return {ok:true,json:async()=>path==='/status'?{output:'/音乐/原始',jobs:[]}:{ok:true}}},setInterval(){},console
});
const run=s=>vm.runInContext(s,context);
(async()=>{
 vm.runInContext(code,context);await new Promise(r=>setImmediate(r));
 assert.equal(run('language'),'ja');assert.equal(context.document.documentElement.lang,'ja');
 assert.equal(run("normalizeLanguage('zh-Hant-TW')"),'zh-TW');
 assert.equal(run("normalizeLanguage('fr-FR')"),null);
 for(const lang of Object.keys(catalog)){
  elements.language.value=lang;await elements.language.onchange();
  assert.equal(context.document.documentElement.lang,lang);
  assert.equal(saved['audio-to-mp3-language'],lang);
  assert.equal(JSON.parse(requests.filter(r=>r.path==='/language').at(-1).options.body).language,lang);
  for(const e of translated)assert.ok(e.textContent,`Missing ${lang}: ${e.dataset.i18n}`);
 }
 run("language='en';applyLanguage();render({output:'/音乐/原始',jobs:[{id:'1',name:'已取消.flac',status:'done',message_source:'转换完成',result:'转换完成.mp3'}]})");
 assert.equal(elements.output.textContent,'/音乐/原始');
 const row=elements.list.children[0];assert.equal(row.children[0].children[0].textContent,'已取消.flac');
 assert.equal(row.children[0].children[1].textContent,'Conversion completed · 转换完成.mp3');
 const before=run('lastState.jobs[0].name');elements.ekey.value='typed-but-not-saved';
 run("language='ja';applyLanguage()");
 assert.equal(elements.ekey.value,'typed-but-not-saved');assert.equal(run('lastState.jobs[0].name'),before);
 assert.equal(elements.list.children[0].children[1].children[0].textContent,'完了');
 run("language='en';notify('uploading',{index:1,total:2,name:'<img onerror=alert(1)>.ncm'})");
 assert.equal(elements.notice.textContent,'Adding 1/2: <img onerror=alert(1)>.ncm');
 run("language='ja';applyLanguage()");assert.match(elements.notice.textContent,/追加中/);
 assert.ok(elements.notice.textContent.includes('<img onerror=alert(1)>.ncm'));
 run("language='en';notifyError({source:'文件名无效。'})");assert.equal(elements.notice.textContent,'Invalid filename.');
 run("language='ja';applyLanguage()");assert.equal(elements.notice.textContent,'無効なファイル名です。');
 // Rapid switches serialize preference writes so the last selection wins.
 requests=[];for(const lang of ['en','ja','zh-TW']){elements.language.value=lang;elements.language.onchange()}
 await new Promise(r=>setImmediate(r));await run('languageSave');
 assert.deepEqual(requests.filter(r=>r.path==='/language').map(r=>JSON.parse(r.options.body).language),['en','ja','zh-TW']);
 assert.equal(requests.filter(r=>r.path==='/language').at(-1).options.headers['X-App-Language'],'zh-TW');
 console.log('UI checks passed: browser-language fallback, four languages, live queue/error/notice translation, data and ekey preservation, serialized preferences.');
})().catch(e=>{console.error(e);process.exitCode=1});
