#!/usr/bin/env node
/* Local file-only visual acceptance. No browser download or deployment. */
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {createHash} = require('node:crypto');
const {execFileSync} = require('node:child_process');
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

async function main() {
  const args = process.argv.slice(2);
  if (args.includes('--help')) {
    console.log('node scripts/package_b_visual_qa.cjs --html FILE --gate FILE --output-dir NEW_DIR [--chromium-path EXISTING_BINARY] [--playwright-module EXISTING_MODULE]');
    return;
  }
  const options = {};
  for (let i = 0; i < args.length; i += 2) {
    if (!['--html','--gate','--output-dir','--chromium-path','--playwright-module'].includes(args[i]) || !args[i+1]) throw Error('Invalid arguments; use --help');
    options[args[i]] = path.resolve(args[i+1]);
  }
  for (const key of ['--html','--gate','--output-dir']) if (!options[key]) throw Error('Missing '+key);
  const htmlPath = options['--html'], output = options['--output-dir'];
  // A fresh output directory prevents following any pre-existing output links.
  if (fs.existsSync(output)) throw Error('Output directory must be new');
  const document = fs.readFileSync(htmlPath);
  const reportSHA = digest(document);
  const gate = JSON.parse(fs.readFileSync(options['--gate'], 'utf8'));
  if (gate.html_sha256 !== reportSHA || gate.publication_gate?.pass !== true ||
      gate.publication_gate.report_sha256 !== reportSHA || gate.publication_gate.critical_count !== 0 ||
      typeof gate.publication_gate.score_total !== 'number' || !Number.isFinite(gate.publication_gate.score_total) ||
      gate.publication_gate.score_total < 95 || gate.production_publication_authorized !== false) throw Error('Unapproved or mismatched synthetic HTML/gate');
  if (!document.includes('data-b-report-version="v1"') || !document.includes('EJEMPLO SINTÉTICO')) throw Error('Only Package B synthetic reports allowed');
  const baseline = JSON.parse(fs.readFileSync(path.join(__dirname,'../tests/fixtures/package_b_visual_contract.json'),'utf8'));
  const {chromium} = require(options['--playwright-module'] || 'playwright');
  const browser = await chromium.launch({headless:true, executablePath:options['--chromium-path'], args:['--no-sandbox','--disable-gpu','--disable-dev-shm-usage']});
  let context;
  try {
    context = await browser.newContext({javaScriptEnabled:false,locale:'es-ES',timezoneId:'UTC',deviceScaleFactor:1});
    const url = pathToFileURL(htmlPath).href;
    const rejected = [];
    await context.route('**/*', route => {
      const request = route.request().url();
      if (request === url || request.startsWith('data:')) return route.continue();
      rejected.push(request); return route.abort();
    });
    fs.mkdirSync(output,{recursive:true});
    const manifest = {contract:baseline.version, html_sha256:reportSHA, browser:browser.version(), snapshots:[], publication_authorized:false};
    for (const width of baseline.viewports) {
      const page = await context.newPage();
      await page.setViewportSize({width,height:1000});
      await page.goto(url,{waitUntil:'load'});
      await page.evaluate(() => document.fonts.ready);
      const layout = await page.evaluate(() => {
        const rect = node => {const r=node.getBoundingClientRect();return {x:Math.round(r.x*100)/100,y:Math.round(r.y*100)/100,width:Math.round(r.width*100)/100,height:Math.round(r.height*100)/100};};
        const title=document.querySelector('h1');const range=document.createRange();range.selectNodeContents(title);
        return {
          body_overflow:document.documentElement.scrollWidth>innerWidth,
          title_ink_width:range.getBoundingClientRect().width,
          sections:[...document.querySelectorAll('.page')].map(rect),
          charts:[...document.querySelectorAll('svg')].map(rect),
          source_series:document.querySelectorAll('tr[data-series-sha256]').length,
          evidence_series:document.querySelectorAll('tr[data-evidence-series-sha256]').length,
          number_overflow:[...document.querySelectorAll('.value,.big-price,.tk-price,.table-wrap td.num')].filter(n=>n.scrollWidth>n.clientWidth+1).map(n=>n.textContent),
          blank_axis_labels:[...document.querySelectorAll('svg text')].filter(n=>n.getBBox().width<1).length,
          unresolved_evidence_links:[...document.querySelectorAll('a[href^="#evidence-"]')].filter(n=>!document.getElementById(n.getAttribute('href').slice(1))).length,
          text:document.body.innerText,
        };
      });
      if (layout.body_overflow || layout.title_ink_width<20 || layout.number_overflow.length || layout.blank_axis_labels || layout.unresolved_evidence_links ||
          layout.sections.length!==baseline.sections || layout.charts.length!==baseline.charts || layout.source_series!==baseline.source_series ||
          layout.evidence_series!==baseline.evidence_series ||
          baseline.required_text.some(text=>!layout.text.includes(text))) throw Error('Layout/legibility failure at '+width+': '+JSON.stringify({...layout,text:undefined}));
      delete layout.text;
      const screenshot = await page.screenshot({fullPage:true,animations:'disabled'});
      const repeated = await page.screenshot({fullPage:true,animations:'disabled'});
      if (digest(screenshot)!==digest(repeated)) throw Error('Nondeterministic screenshot at '+width);
      fs.writeFileSync(path.join(output,'viewport-'+width+'.png'),screenshot);
      manifest.snapshots.push({width,png_sha256:digest(screenshot),layout});
      await page.close();
    }
    const page = await context.newPage(); await page.goto(url,{waitUntil:'load'});
    await page.evaluate(() => document.fonts.ready);
    const pdfPath = path.join(output,'package_b_synthetic_report.pdf');
    const pdf = await page.pdf({format:'A4',printBackground:true,preferCSSPageSize:true});
    fs.writeFileSync(pdfPath,pdf);
    const info = execFileSync('pdfinfo',[pdfPath],{encoding:'utf8'});
    const pdfPages = Number(info.match(/^Pages:\s+(\d+)$/m)?.[1]);
    const rawPDFText = execFileSync('pdftotext',[pdfPath,'-'],{encoding:'utf8'});
    const pdfText = rawPDFText.replace(/\s+/g,' ');
    const pageTextLengths = rawPDFText.split('\f').slice(0,pdfPages).map(text=>text.trim().length);
    if (pdfPages<baseline.minimum_pdf_pages || pdfPages>baseline.maximum_pdf_pages || !/Page size:.*A4/.test(info) ||
        pageTextLengths.length!==pdfPages || pageTextLengths.some(length=>length<baseline.minimum_text_characters_per_pdf_page) ||
        baseline.required_text.some(text=>!pdfText.includes(text))) throw Error('A4 pagination or text failure');
    if (rejected.length) throw Error('Unexpected resource requests: '+rejected.length);
    manifest.pdf = {pages:pdfPages,sha256:digest(pdf),format:'A4',page_text_lengths:pageTextLengths};
    manifest.unexpected_resource_requests = rejected.length;
    manifest.state = 'VISUAL_STRUCTURE_PASS_MANUAL_REVIEW_REQUIRED';
    fs.writeFileSync(path.join(output,'visual_manifest.json'),JSON.stringify(manifest,null,2)+'\n');
    console.log(JSON.stringify({state:manifest.state,browser:manifest.browser,viewports:baseline.viewports,pdf_pages:pdfPages,unexpected_resource_requests:rejected.length,html_sha256:reportSHA}));
  } finally {
    if (context) await context.close();
    await browser.close();
  }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
