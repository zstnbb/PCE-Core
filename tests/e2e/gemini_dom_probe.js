// SPDX-License-Identifier: Apache-2.0
/**
 * Gemini DOM Diagnostic Probe
 * 
 * Run this in the browser console on gemini.google.com after getting
 * a response with code blocks, citations, images, etc.
 * 
 * Copy-paste into Chrome DevTools console to diagnose DOM structure.
 */
(function() {
  const results = {
    timestamp: new Date().toISOString(),
    url: location.href,
    turnSelectors: {},
    codeBlocks: [],
    citations: [],
    images: [],
    toolIndicators: [],
    thinkingPanels: [],
    customElements: [],
    modelResponses: [],
  };

  // Test all turn selectors
  const selectors = {
    "model-response": "model-response",
    "user-query": "user-query",
    "message-content": "message-content",
    "code-block": "code-block",
    "[data-turn-role]": "[data-turn-role]",
    ".response-container": ".response-container",
    ".model-response-text": ".model-response-text",
    ".markdown": ".markdown",
    ".markdown-main-panel": ".markdown-main-panel",
    "pre": "pre",
    "pre > code": "pre > code",
    "code": "code",
    "a[href]": "a[href]",
    "img": "img",
    "details": "details",
    "summary": "summary",
    ".chip": ".chip",
    "[class*='citation']": "[class*='citation']",
    "[class*='source']": "[class*='source']",
    "[class*='reference']": "[class*='reference']",
    "[class*='grounding']": "[class*='grounding']",
    "[class*='search']": "[class*='search']",
    "[class*='tool']": "[class*='tool']",
    "[class*='think']": "[class*='think']",
    "[class*='thought']": "[class*='thought']",
    "[class*='reason']": "[class*='reason']",
    "[class*='code']": "[class*='code']",
    "[class*='file']": "[class*='file']",
    "[class*='upload']": "[class*='upload']",
    "[class*='image']": "[class*='image']",
    "[class*='canvas']": "[class*='canvas']",
    "[class*='artifact']": "[class*='artifact']",
    "cite-source": "cite-source",
    "cite-button": "cite-button",
    "web-search-tool": "web-search-tool",
    "search-result": "search-result",
    "[class*='footnote']": "[class*='footnote']",
    "[class*='generated']": "[class*='generated']",
  };

  for (const [label, sel] of Object.entries(selectors)) {
    try {
      const els = document.querySelectorAll(sel);
      if (els.length > 0) {
        results.turnSelectors[label] = {
          count: els.length,
          samples: Array.from(els).slice(0, 3).map(el => ({
            tag: el.tagName.toLowerCase(),
            class: (el.className || "").toString().slice(0, 200),
            id: el.id || "",
            text: (el.innerText || "").slice(0, 100),
            children: el.children.length,
            attrs: Array.from(el.attributes || []).map(a => `${a.name}=${a.value.slice(0,50)}`).join(", "),
          })),
        };
      }
    } catch (e) {}
  }

  // Probe model-response elements specifically
  document.querySelectorAll("model-response").forEach((mr, i) => {
    const info = {
      index: i,
      outerHTML_snippet: mr.outerHTML.slice(0, 500),
      children: Array.from(mr.children).map(c => ({
        tag: c.tagName.toLowerCase(),
        class: (c.className || "").toString().slice(0, 200),
        childCount: c.children.length,
      })),
      hasPre: mr.querySelectorAll("pre").length,
      hasCode: mr.querySelectorAll("code").length,
      hasCodeBlock: mr.querySelectorAll("code-block").length,
      hasImg: mr.querySelectorAll("img").length,
      hasLink: mr.querySelectorAll("a[href]").length,
      hasDetails: mr.querySelectorAll("details").length,
      markdown: null,
    };
    
    const md = mr.querySelector(".markdown, .markdown-main-panel, message-content");
    if (md) {
      info.markdown = {
        tag: md.tagName.toLowerCase(),
        class: (md.className || "").toString().slice(0, 200),
        text: md.innerText.slice(0, 200),
      };
    }
    
    results.modelResponses.push(info);
  });

  // Probe code blocks
  document.querySelectorAll("pre, code-block, [class*='code-block']").forEach((el, i) => {
    if (i >= 5) return;
    results.codeBlocks.push({
      tag: el.tagName.toLowerCase(),
      class: (el.className || "").toString().slice(0, 200),
      parentTag: el.parentElement?.tagName?.toLowerCase(),
      parentClass: (el.parentElement?.className || "").toString().slice(0, 200),
      hasCodeChild: !!el.querySelector("code"),
      codeClass: el.querySelector("code")?.className || "",
      text: (el.innerText || "").slice(0, 200),
      prevSibling: el.previousElementSibling ? {
        tag: el.previousElementSibling.tagName.toLowerCase(),
        class: (el.previousElementSibling.className || "").toString().slice(0, 100),
        text: el.previousElementSibling.innerText?.slice(0, 50),
      } : null,
      outerHTML: el.outerHTML.slice(0, 500),
    });
  });

  // Probe links/citations in model responses
  document.querySelectorAll("model-response a[href], .markdown a[href]").forEach((a, i) => {
    if (i >= 10) return;
    results.citations.push({
      href: a.href?.slice(0, 200),
      text: a.innerText?.slice(0, 100),
      class: (a.className || "").toString().slice(0, 200),
      parentClass: (a.parentElement?.className || "").toString().slice(0, 100),
      isExternal: !a.href?.includes(location.hostname),
    });
  });

  // Probe all custom elements (non-standard tags)
  const allEls = document.querySelectorAll("*");
  const customTags = new Set();
  allEls.forEach(el => {
    const tag = el.tagName.toLowerCase();
    if (tag.includes("-") && !customTags.has(tag)) {
      customTags.add(tag);
    }
  });
  results.customElements = Array.from(customTags).sort();

  console.log("=== Gemini DOM Probe Results ===");
  console.log(JSON.stringify(results, null, 2));
  
  // Summary
  console.log("\n=== Quick Summary ===");
  for (const [sel, info] of Object.entries(results.turnSelectors)) {
    console.log(`  ${sel}: ${info.count} found`);
  }
  console.log(`  Custom elements: ${results.customElements.join(", ")}`);
  console.log(`  Model responses: ${results.modelResponses.length}`);
  console.log(`  Code blocks: ${results.codeBlocks.length}`);
  console.log(`  Citations: ${results.citations.length}`);
  
  // Copy to clipboard if possible
  try {
    copy(JSON.stringify(results, null, 2));
    console.log("\n✓ Results copied to clipboard!");
  } catch(e) {
    console.log("\n(Could not copy to clipboard. Use copy() manually.)");
  }
  
  return results;
})();
