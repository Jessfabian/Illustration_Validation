(async function () {
  "use strict";

  if (document.readyState === "loading") {
    await new Promise(function (resolve) {
      document.addEventListener("DOMContentLoaded", resolve, { once: true });
    });
  }

  var $ = function (id) {
    return document.getElementById(id);
  };
  var files = { app: null, ill: null };
  var docs = { app: null, ill: null };
  var pdfReady = false;
  var LEARNING_KEY = "illustrationValidatorLearningV3";
  var AUTO_APPLY_FIELDS = [
    "plan",
    "apl",
    "loanRate",
    "dividend",
    "waiver",
    "lisr",
    "billing",
    "frequency",
    "signatureEvidence",
  ];

  var fields = [
    ["insuredName", "Insured name", "text"],
    ["plan", "Plan", "text"],
    ["faceAmount", "Face amount", "text"],
    ["apl", "Automatic Premium Loan", "choice"],
    ["loanRate", "Loan rate", "text"],
    ["dividend", "Dividend option", "text"],
    ["waiver", "Waiver of Premium", "text"],
    ["gir", "GIR", "text"],
    ["lisr", "LISR", "choice"],
    ["billing", "Billing type", "text"],
    ["frequency", "Payment frequency", "text"],
    ["createdDates", "Created date(s)", "text"],
    ["signedDate", "Signature date", "text"],
    ["signatureEvidence", "Signature evidence", "signature"],
  ];

  var templates = {
    plan: function (a, b) {
      return (
        "Plan: The Part 1 indicates " +
        a +
        " however, the illustration indicates " +
        b +
        "."
      );
    },
    faceAmount: function (a, b) {
      return (
        "Face amount: The Part 1 indicates " +
        a +
        " however, the illustration indicates " +
        b +
        "."
      );
    },
    apl: function (a, b) {
      return (
        "Automatic Premium Loan: The Part 1 indicates " +
        a +
        " however, the illustration indicates " +
        b +
        "."
      );
    },
    loanRate: function (a, b) {
      return (
        "Loan rate: The Part 1 indicates " +
        a +
        " however, the illustration indicates " +
        b +
        "."
      );
    },
    dividend: function (a, b) {
      return (
        "Dividend: The Part 1 indicates " +
        a +
        " however, the illustration indicates " +
        b +
        "."
      );
    },
    waiver: function (a, b) {
      return (
        "Waiver of Premium: The Part 1 indicates " +
        a +
        " however, the illustration is run " +
        b +
        "."
      );
    },
    gir: function (a, b) {
      return (
        "GIR: The Part 1 indicates " +
        a +
        " however, the illustration is run with a GIR of " +
        b +
        "."
      );
    },
    frequency: function (a, b) {
      return (
        "Payment frequency: The Part 1 indicates " +
        a +
        " however, the illustration is run at " +
        b +
        "."
      );
    },
    annualPac: function () {
      return "Please verify payment frequency. The Part 1 indicates Annual PAC however, that is not an option. The options would be Monthly PAC or Annual Individual direct bill / Group bill.";
    },
    nyUnsigned: function () {
      return "Please submit a signed sales illustration matching the Part 1. The one submitted is unsigned. This is required for all NY policies per Reg 74.";
    },
    lisrUnsigned: function () {
      return "Please submit an unsigned sales illustration as there is LISR on this policy.";
    },
  };

  function clean(value) {
    return String(value == null ? "" : value)
      .replace(/\u00a0/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }
  function normalized(value) {
    return clean(value)
      .toLowerCase()
      .replace(/[,$%]/g, "")
      .replace(/[‐‑‒–—]/g, "-")
      .replace(/\bwl\s*(\d+)\b/g, "whole life $1")
      .replace(/\bwholelife\s*(\d+)\b/g, "whole life $1")
      .replace(/paid[ -]?up additions?|\bpua\b/g, "paid-up additions")
      .replace(/semi annual/g, "semi-annual")
      .replace(/monthly pac/g, "monthly")
      .replace(/annual individual direct bill|annual direct bill/g, "annual")
      .replace(/pre[- ]?authorized check/g, "pac");
  }
  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      }[c];
    });
  }
  function item(value, confidence, meta) {
    return Object.assign(
      {
        v: value || "",
        c: Number(confidence) || 0,
        originalValue: value || "",
        corrected: false,
        confirmed: false,
        reviewDecision: "",
      },
      meta || {},
    );
  }
  function fieldLabel(key) {
    var found = fields.find(function (field) {
      return field[0] === key;
    });
    return found ? found[1] : key;
  }
  function showError(message) {
    var box = $("errorBox");
    if (!box) {
      if (message) console.error(message);
      return;
    }
    box.style.display = message ? "block" : "none";
    box.textContent = message || "";
  }

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var existing = document.querySelector('script[src="' + src + '"]');
      if (existing) {
        if (
          (src.indexOf("pdf") >= 0 && window.pdfjsLib) ||
          (src.indexOf("tesseract") >= 0 && window.Tesseract)
        ) {
          resolve();
          return;
        }
        existing.addEventListener("load", resolve, { once: true });
        existing.addEventListener("error", reject, { once: true });
        return;
      }
      var script = document.createElement("script");
      script.src = src;
      script.async = true;
      script.onload = resolve;
      script.onerror = function () {
        reject(new Error("Unable to load " + src));
      };
      document.head.appendChild(script);
    });
  }

  try {
    await loadScript(
      "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js",
    );
    if (!window.pdfjsLib)
      throw new Error("PDF.js loaded but pdfjsLib was not found.");
    window.pdfjsLib.GlobalWorkerOptions.workerSrc =
      "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
    pdfReady = true;
  } catch (error) {
    console.error(error);
    showError(
      "PDF.js could not load. Use an approved hosted copy of the library.",
    );
  }
  try {
    await loadScript(
      "https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js",
    );
  } catch (error) {
    console.warn("OCR library unavailable:", error);
  }

  function getLearningRules() {
    try {
      return JSON.parse(localStorage.getItem(LEARNING_KEY) || "[]");
    } catch (error) {
      console.warn(error);
      return [];
    }
  }
  function saveLearningRules(rules) {
    try {
      localStorage.setItem(LEARNING_KEY, JSON.stringify(rules));
    } catch (error) {
      console.warn(error);
    }
  }
  function ruleId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }
  function formatLearningScope(rule) {
    return rule.autoApply ? "Automatically applies" : "Reviewed example only";
  }

  function renderLearningRules() {
    var list = $("learningRuleList");
    var empty = $("learningEmptyState");
    var rules = getLearningRules();
    if ($("reviewLearnedCount"))
      $("reviewLearnedCount").textContent = String(rules.length);
    if (!list) return;
    list.innerHTML = "";
    if (!rules.length) {
      if (empty) {
        empty.style.display = "block";
        empty.textContent = "No correction rules have been saved yet.";
      }
      return;
    }
    if (empty) empty.style.display = "none";
    rules.forEach(function (rule) {
      var card = document.createElement("article");
      card.className = "learning-rule";
      card.innerHTML =
        '<div class="learning-rule-content"><div class="learning-rule-heading"><strong>' +
        escapeHtml(rule.fieldLabel || rule.key) +
        '</strong><span class="learning-rule-badge ' +
        (rule.autoApply ? "learning-rule-auto" : "learning-rule-example") +
        '">' +
        escapeHtml(formatLearningScope(rule)) +
        '</span></div><div class="learning-rule-values"><div class="learning-before"><small>Extracted</small><span>' +
        escapeHtml(rule.originalValue || "No value extracted") +
        '</span></div><div class="learning-arrow">→</div><div class="learning-after"><small>Corrected</small><span>' +
        escapeHtml(rule.correctedValue || "No corrected value") +
        '</span></div></div><div class="learning-rule-meta"><span>' +
        escapeHtml(rule.kind === "app" ? "Part 1" : "Illustration") +
        "</span><span>" +
        escapeHtml(rule.documentType || "Unknown document type") +
        "</span>" +
        (rule.pageType
          ? "<span>" + escapeHtml(rule.pageType) + "</span>"
          : "") +
        '</div></div><button type="button" class="learning-rule-delete" data-delete-learning-rule="' +
        escapeHtml(rule.id) +
        '">Delete</button>';
      list.appendChild(card);
    });
    list
      .querySelectorAll("[data-delete-learning-rule]")
      .forEach(function (button) {
        button.addEventListener("click", function () {
          deleteLearningRule(button.dataset.deleteLearningRule);
        });
      });
  }
  function deleteLearningRule(id) {
    saveLearningRules(
      getLearningRules().filter(function (rule) {
        return rule.id !== id;
      }),
    );
    renderLearningRules();
  }
  function learnCorrection(kind, key, originalValue, correctedValue, meta) {
    var original = clean(originalValue),
      corrected = clean(correctedValue);
    if (!corrected || normalized(original) === normalized(corrected)) return;
    var rules = getLearningRules();
    var documentType =
      meta && meta.documentType ? meta.documentType : "unknown";
    var autoApply = AUTO_APPLY_FIELDS.indexOf(key) >= 0 && !!original;
    var duplicate = rules.some(function (rule) {
      return (
        rule.kind === kind &&
        rule.key === key &&
        rule.documentType === documentType &&
        normalized(rule.originalValue) === normalized(original) &&
        normalized(rule.correctedValue) === normalized(corrected)
      );
    });
    if (!duplicate) {
      rules.push({
        id: ruleId(),
        kind: kind,
        key: key,
        fieldLabel: fieldLabel(key),
        documentType: documentType,
        matchedLabel: meta && meta.matchedLabel ? meta.matchedLabel : "",
        pageType: meta && meta.pageType ? meta.pageType : "",
        originalValue: original,
        correctedValue: corrected,
        autoApply: autoApply,
        correctionExampleOnly: !autoApply,
        createdAt: new Date().toISOString(),
      });
      saveLearningRules(rules);
    }
  }
  function applyLearning(kind, fieldMap, documentType) {
    var rules = getLearningRules();
    Object.keys(fieldMap).forEach(function (key) {
      var entry = fieldMap[key];
      var rule = rules.find(function (saved) {
        return (
          saved.autoApply === true &&
          saved.kind === kind &&
          saved.key === key &&
          normalized(saved.originalValue) === normalized(entry.v) &&
          (saved.documentType === documentType ||
            saved.documentType === "unknown")
        );
      });
      if (rule) {
        entry.originalValue = entry.v;
        entry.v = rule.correctedValue;
        entry.c = 100;
        entry.corrected = true;
        entry.learned = true;
        entry.learningRuleId = rule.id;
      }
    });
    return fieldMap;
  }

  function classifyDocument(text) {
    var sample = normalized(text.slice(0, 60000));
    if (/survivorship|second insured|2nd insured/.test(sample))
      return "survivorship";
    if (/vantage term|term life/.test(sample)) return "term";
    if (/whole life/.test(sample)) return "whole-life";
    return "unknown";
  }
  function pageType(text) {
    if (/client input summary/i.test(text)) return "Client Input Summary";
    if (/policy assumptions|whole life insurance summary/i.test(text))
      return "Policy assumptions";
    if (/numeric summary/i.test(text)) return "Numeric summary";
    if (/prepared for|presented by/i.test(text))
      return "Cover or narrative summary";
    return "Other";
  }
  function sourceWeight(type) {
    return (
      {
        "Client Input Summary": 22,
        "Policy assumptions": 16,
        "Cover or narrative summary": 12,
        "Numeric summary": 6,
        Other: 0,
      }[type] || 0
    );
  }
  function firstCandidate(pages, patterns, parser) {
    var candidates = [];
    pages.forEach(function (page, pageIndex) {
      patterns.forEach(function (pattern, patternIndex) {
        var match = pattern.exec(page.text);
        pattern.lastIndex = 0;
        if (!match) return;
        var raw = clean(match[1] || match[0]);
        var value = parser ? parser(raw) : raw;
        if (!value) return;
        var type = pageType(page.text);
        candidates.push({
          value: value,
          confidence: Math.min(
            98,
            62 + sourceWeight(type) + Math.max(0, 10 - patternIndex * 3),
          ),
          pageNumber: page.pageNumber || pageIndex + 1,
          pageType: type,
          matchedLabel: match[0]
            .slice(0, Math.max(0, match[0].length - raw.length))
            .trim(),
          extractionMethod: "Prioritized multi-pattern match",
        });
      });
    });
    candidates.sort(function (a, b) {
      return b.confidence - a.confidence;
    });
    return candidates[0] || null;
  }
  function money(value) {
    var match = String(value || "").match(/\$?\s*([\d,]+(?:\.\d{2})?)/);
    return match ? "$" + match[1] : "";
  }
  function candidateItem(candidate, documentType) {
    return candidate
      ? item(
          candidate.value,
          candidate.confidence,
          Object.assign({ documentType: documentType }, candidate),
        )
      : item("", 0, { documentType: documentType });
  }

  function cleanPlanValue(value) {
    var text = clean(value)
      .replace(/^[;:|\-\s]+/, "")
      .replace(/\s*[|;]\s*\d+\.\s*(?:Additional|Life|Rider|ALIR|LISR).*$/i, "")
      .replace(/\s+\d+\.\s*(?:Additional|Life|Rider|ALIR|LISR).*$/i, "")
      .replace(/\bWholeLife\b/gi, "Whole Life")
      .replace(/\bWL\s*(\d+)\b/gi, "Whole Life $1")
      .replace(/\s+/g, " ")
      .trim();
    var known = text.match(
      /\b(?:Survivorship\s+)?Whole\s*Life\s*(?:Legacy\s*)?\d+\b/i,
    );
    if (known) return known[0].replace(/\s+/g, " ").trim();
    var term = text.match(/\bVantage\s+Term\s+\d+\b/i);
    return term ? term[0].replace(/\s+/g, " ").trim() : text;
  }
  function parsePlanFromPages(pages) {
    return firstCandidate(
      pages,
      [
        /(?:^|\n)\s*2\.\s*Plan\s*[:\-]?\s*[;|]?\s*((?:Survivorship\s+)?Whole\s*Life\s*(?:Legacy\s*)?\d+|WholeLife\d+|WL\s*\d+|Vantage\s+Term\s+\d+)/im,
        /(?:Policy Name|Product Name|Product|Plan)\s*[:\-]?\s*((?:Survivorship\s+)?Whole\s*Life\s*(?:Legacy\s*)?\d+|WholeLife\d+|WL\s*\d+|Vantage\s+Term\s+\d+)/i,
        /\b((?:Survivorship\s+)?Whole\s*Life\s*(?:Legacy\s*)?\d+|WholeLife\d+|WL\s*\d+|Vantage\s+Term\s+\d+)\b/i,
      ],
      cleanPlanValue,
    );
  }
  function cleanDividendValue(value) {
    var text = clean(value);
    if (/paid[-\s]?up additions?|\bPUA\b/i.test(text))
      return "Paid-Up Additions";
    if (/supplemental insurance dividend|\bSID\b|dividend\/flex/i.test(text))
      return "Supplemental Insurance Dividend/Flex";
    if (/dividend accumulations?|accumulate at interest/i.test(text))
      return "Dividend Accumulations";
    if (/\bcash\b/i.test(text)) return "Cash";
    if (/reduce premium|premium reduction/i.test(text)) return "Reduce Premium";
    return /planned ALIR|additional life insurance rider|select one/i.test(text)
      ? ""
      : text;
  }
  function parseDividendFromPages(pages) {
    return firstCandidate(
      pages,
      [
        /(?:Initial Base Dividend Option|Initial Dividend Option|Dividend Option)\s*[:\-]?\s*(?:\d+\s+to\s+\d+\s*[-:]\s*)?(Paid[-\s]?Up Additions?|PUA|Cash|Supplemental Insurance Dividend\/Flex|Supplemental Insurance Dividend|Dividend Accumulations?|Accumulate at Interest|Reduce Premium)/i,
        /Dividend Option[^\n]{0,80}\b(Paid[-\s]?Up Additions?|PUA|Cash|Supplemental Insurance Dividend\/Flex|Supplemental Insurance Dividend|Dividend Accumulations?|Accumulate at Interest|Reduce Premium)\b/i,
        /(?:☒|✓|■|●|X)\s*(Paid[-\s]?Up Additions?|PUA|Cash|Supplemental Insurance Dividend\/Flex|Dividend Accumulations?|Accumulate at Interest|Reduce Premium)/i,
      ],
      cleanDividendValue,
    );
  }
  function cleanWaiverValue(value) {
    var text = clean(value);
    if (/\[\s*WP\s*\]\s*N\b|\bWP\s*[:\-]?\s*N\b/i.test(text)) return "No";
    if (/\[\s*WP\s*\]\s*Y\b|\bWP\s*[:\-]?\s*Y\b/i.test(text)) return "Yes";
    if (/\bwithout waiver\b|not included|\bnone\b|\bno\b/i.test(text))
      return "No";
    if (/\bincluded\b|\byes\b/i.test(text)) return "Yes";
    if (/\binsured 1\b/i.test(text)) return "Insured 1";
    if (/\binsured 2\b/i.test(text)) return "Insured 2";
    return "";
  }
  function parseWaiverFromPages(pages) {
    return firstCandidate(
      pages,
      [
        /Waiver of Premium[^\n]{0,100}(\[\s*WP\s*\]\s*[YN])/i,
        /Waiver of Premium[^\n]{0,100}\b(Yes|No|Included|Not Included|None|Insured 1|Insured 2)\b/i,
        /(\[\s*WP\s*\]\s*[YN])/i,
        /Waiver of Premium[^\n]{0,120}(?:☒|✓|■|●|X)\s*(Yes|No|Insured 1|Insured 2)/i,
      ],
      cleanWaiverValue,
    );
  }
  function cleanAplValue(value) {
    var text = clean(value);
    if (/\[\s*APL\s*\]\s*Y\b|\bAPL\s*[:\-]?\s*Y\b/i.test(text)) return "Yes";
    if (/\[\s*APL\s*\]\s*N\b|\bAPL\s*[:\-]?\s*N\b/i.test(text)) return "No";
    if (/\bnot included\b|\bnone\b|\bno\b/i.test(text)) return "No";
    if (/\bincluded\b|\byes\b/i.test(text)) return "Yes";
    return "";
  }
  function parseAplFromPages(pages) {
    return firstCandidate(
      pages,
      [
        /Automatic Premium Loan[^\n]{0,100}(\[\s*APL\s*\]\s*[YN])/i,
        /(?:^|\n)\s*(\[\s*APL\s*\]\s*[YN])(?:\s|$)/im,
        /Automatic Premium Loan(?:\s*\(APL\))?[^\n]{0,90}\b(Yes|No|Included|Not Included)\b/i,
        /Automatic Premium Loan(?:\s*\(APL\))?[^\n]{0,100}(?:☒|✓|■|●|X)\s*(Yes|No)/i,
      ],
      cleanAplValue,
    );
  }

  function parseDocument(kind, pages, text) {
    var documentType = classifyDocument(text);
    var result = {};
    if (kind === "app") {
      result.insuredName = candidateItem(
        firstCandidate(pages, [
          /Full legal name \(First, MI, Last, Suffix\)\s*[:\-]?\s*([^\n]{2,80})/i,
          /Proposed Insured(?: Name)?\s*[:\-]?\s*([^\n]{2,80})/i,
        ]),
        documentType,
      );
      result.faceAmount = candidateItem(
        firstCandidate(
          pages,
          [/(?:Face amount|Amount of Insurance)\s*[:\-]?\s*([^\n]{1,50})/i],
          money,
        ),
        documentType,
      );
    } else {
      result.insuredName = candidateItem(
        firstCandidate(pages, [
          /(?:Prepared for|Proposed Insured(?: Name)?|Insured Name)\s*[:\-]?\s*([^\n]{2,85})/i,
        ]),
        documentType,
      );
      result.faceAmount = candidateItem(
        firstCandidate(
          pages,
          [
            /(?:Base Policy Face Amount|Base Face Amount|Initial Face Amount|Face Amount|Initial Death Benefit)\s*[:\-]?\s*([^\n]{1,60})/i,
          ],
          money,
        ),
        documentType,
      );
    }
    result.plan = candidateItem(parsePlanFromPages(pages), documentType);
    result.apl = candidateItem(parseAplFromPages(pages), documentType);
    result.dividend = candidateItem(
      parseDividendFromPages(pages),
      documentType,
    );
    result.waiver = candidateItem(parseWaiverFromPages(pages), documentType);
    result.loanRate = candidateItem(
      firstCandidate(pages, [
        /Loan (?:Interest )?Rate[^\n]{0,80}\b(Adjustable|Fixed)\b/i,
      ]),
      documentType,
    );
    result.gir = candidateItem(
      firstCandidate(
        pages,
        [/(?:Guaranteed Insurability Rider|\bGIR\b)\s*[:\-]?\s*([^\n]{1,65})/i],
        function (v) {
          return money(v) || clean(v);
        },
      ),
      documentType,
    );
    result.lisr = item(
      /Life Insurance Supplement Rider|\bLISR\b/i.test(text) ? "Yes" : "No",
      86,
      {
        documentType: documentType,
        extractionMethod: "Document presence check",
      },
    );
    result.billing = candidateItem(
      firstCandidate(pages, [
        /(?:Billing Type|Payment Method)\s*[:\-]?\s*([^\n]{2,90})/i,
        /Billing type[^\n]{0,80}\b(Pre-Authorized Check|Individual Direct Bill|Group Bill|PAC)\b/i,
      ]),
      documentType,
    );
    result.frequency = candidateItem(
      firstCandidate(
        pages,
        [
          /(?:Premium Payment Mode|Premium Mode|Payment Frequency|Premium Frequency|Frequency|Mode)\s*[:\-]?\s*([^\n]{2,60})/i,
        ],
        function (v) {
          var match = v.match(
            /\b(Monthly|Quarterly|Semi-annual|Semi Annual|Annual|Single Premium)\b/i,
          );
          return match ? match[1] : clean(v);
        },
      ),
      documentType,
    );
    var created = [],
      rx =
        /(?:Created|Creation|Prepared) Date\s*[:\-]?\s*(\d{1,2}\/\d{1,2}\/\d{2,4})/gi,
      m;
    while ((m = rx.exec(text)) !== null)
      if (created.indexOf(m[1]) < 0) created.push(m[1]);
    result.createdDates = item(created.join(", "), created.length ? 88 : 15, {
      documentType: documentType,
      extractionMethod: "All-date collection",
    });
    result.signedDate = candidateItem(
      firstCandidate(pages, [
        /(?:Owner|Producer|Proposed Insured)?\s*Signature[^\n]{0,100}?Date\s*[:\-]?\s*(\d{1,2}\/\d{1,2}\/\d{2,4})/i,
      ]),
      documentType,
    );
    result.signatureEvidence = item(
      /docusign envelope id|owner signature|producer signature|proposed insured signature|signature of/i.test(
        text,
      )
        ? "Manual review"
        : "Not found",
      35,
      {
        documentType: documentType,
        extractionMethod: "Signature text presence check",
      },
    );
    return applyLearning(kind, result, documentType);
  }

  async function extractPdf(file, kind) {
    if (!window.pdfjsLib) throw new Error("PDF.js is unavailable.");
    var pdf = await window.pdfjsLib.getDocument({
      data: await file.arrayBuffer(),
    }).promise;
    var pages = [],
      progress = $(kind + "Progress");
    for (var pageNumber = 1; pageNumber <= pdf.numPages; pageNumber++) {
      var page = await pdf.getPage(pageNumber),
        content = await page.getTextContent(),
        lines = {};
      content.items.forEach(function (textItem) {
        var y =
          Math.round(((textItem.transform && textItem.transform[5]) || 0) / 3) *
          3;
        if (!lines[y]) lines[y] = [];
        lines[y].push({
          x: (textItem.transform && textItem.transform[4]) || 0,
          text: textItem.str,
        });
      });
      var pageText = Object.keys(lines)
        .sort(function (a, b) {
          return Number(b) - Number(a);
        })
        .map(function (y) {
          return lines[y]
            .sort(function (a, b) {
              return a.x - b.x;
            })
            .map(function (part) {
              return part.text;
            })
            .join(" ");
        })
        .join("\n");
      pages.push({ pageNumber: pageNumber, text: pageText });
      if (progress)
        progress.style.width =
          Math.round((pageNumber / pdf.numPages) * 70) + "%";
    }
    var text = pages
      .map(function (page) {
        return page.text;
      })
      .join("\n\f\n");
    if (clean(text).length < 300 && window.Tesseract) {
      pages = [];
      var maximumPages = Math.min(pdf.numPages, 15);
      for (var n = 1; n <= maximumPages; n++) {
        var ocrPage = await pdf.getPage(n),
          viewport = ocrPage.getViewport({ scale: 2 }),
          canvas = document.createElement("canvas"),
          context = canvas.getContext("2d");
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        await ocrPage.render({ canvasContext: context, viewport: viewport })
          .promise;
        var recognized = await window.Tesseract.recognize(canvas, "eng");
        pages.push({
          pageNumber: n,
          text: recognized && recognized.data ? recognized.data.text : "",
        });
        if (progress)
          progress.style.width = 70 + Math.round((n / maximumPages) * 30) + "%";
      }
      text = pages
        .map(function (page) {
          return page.text;
        })
        .join("\n\f\n");
    }
    if (progress) progress.style.width = "100%";
    return {
      text: text,
      pages: pages,
      fields: parseDocument(kind, pages, text),
      pageCount: pdf.numPages,
      documentType: classifyDocument(text),
    };
  }

  function isPdf(file) {
    return (
      !!file && (file.type === "application/pdf" || /\.pdf$/i.test(file.name))
    );
  }
  function updateButtons() {
    if ($("extractBtn"))
      $("extractBtn").disabled = !(files.app && files.ill && pdfReady);
    if ($("compareBtn")) $("compareBtn").disabled = true;
    if (!$("runStatus")) return;
    if (!pdfReady) {
      $("runStatus").textContent = "PDF library unavailable";
      $("runStatus").className = "status bad";
    } else if (files.app && files.ill) {
      $("runStatus").textContent = "Ready to extract";
      $("runStatus").className = "status good";
    } else {
      $("runStatus").textContent = "Waiting for two PDFs";
      $("runStatus").className = "status pending";
    }
  }
  function setFile(kind, file) {
    showError("");
    if (!isPdf(file)) {
      showError("Please choose a PDF file.");
      return;
    }
    files[kind] = file;
    docs[kind] = null;
    if ($(kind + "Meta"))
      $(kind + "Meta").textContent =
        file.name + " • " + (file.size / 1048576).toFixed(2) + " MB";
    if ($(kind + "Progress")) $(kind + "Progress").style.width = "0%";
    updateButtons();
  }
  function setupDropZone(zoneId, inputId, kind) {
    var zone = $(zoneId),
      input = $(inputId);
    if (!zone || !input) return;
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        event.stopPropagation();
        zone.classList.add("drag");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        event.stopPropagation();
        zone.classList.remove("drag");
      });
    });
    zone.addEventListener("drop", function (event) {
      if (event.dataTransfer && event.dataTransfer.files[0])
        setFile(kind, event.dataTransfer.files[0]);
    });
    input.addEventListener("change", function () {
      if (input.files && input.files[0]) setFile(kind, input.files[0]);
    });
    zone.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        input.click();
      }
    });
  }
  ["dragenter", "dragover", "dragleave", "drop"].forEach(function (name) {
    document.addEventListener(name, function (event) {
      event.preventDefault();
    });
  });
  setupDropZone("appDrop", "appFile", "app");
  setupDropZone("illDrop", "illFile", "ill");

  function makeControl(key, type, value) {
    if (type === "choice")
      return (
        '<select data-key="' +
        escapeHtml(key) +
        '"><option value=""></option><option value="Yes">Yes</option><option value="No">No</option></select>'
      );
    if (type === "signature")
      return (
        '<select data-key="' +
        escapeHtml(key) +
        '"><option value=""></option><option value="Signed">Signed</option><option value="Unsigned">Unsigned</option><option value="Manual review">Manual review</option><option value="Not found">Not found</option></select>'
      );
    return (
      '<input type="text" data-key="' +
      escapeHtml(key) +
      '" value="' +
      escapeHtml(value) +
      '">'
    );
  }

  function renderFields(kind) {
    var host = $(kind + "Fields"),
      doc = docs[kind];
    if (!host || !doc) return;
    host.innerHTML = "";
    fields.forEach(function (definition) {
      var key = definition[0],
        label = definition[1],
        type = definition[2],
        entry = doc.fields[key] || item("", 0),
        box = document.createElement("div");
      box.className =
        "field " + (entry.c >= 85 ? "high" : entry.c < 50 ? "low" : "");
      var metadata = [
        entry.pageNumber ? "page " + entry.pageNumber : "",
        entry.pageType || "",
        entry.learned ? "learned correction applied" : "",
      ]
        .filter(Boolean)
        .join(" • ");
      box.innerHTML =
        "<small>" +
        escapeHtml(label) +
        " • confidence " +
        escapeHtml(entry.c) +
        "%" +
        (metadata ? " • " + escapeHtml(metadata) : "") +
        "</small>" +
        makeControl(key, type, entry.v) +
        '<div class="field-review-actions" role="group" aria-label="Review ' +
        escapeHtml(label) +
        '">' +
        '<button type="button" class="btn secondary field-correct" data-correct="' +
        escapeHtml(key) +
        '">Correct</button>' +
        '<button type="button" class="btn secondary field-incorrect" data-incorrect="' +
        escapeHtml(key) +
        '">Incorrect</button>' +
        '<button type="button" class="btn primary save-correction" data-save="' +
        escapeHtml(key) +
        '" hidden>Save correction</button></div>' +
        '<div class="field-learning-message" data-message="' +
        escapeHtml(key) +
        '"></div>';
      host.appendChild(box);
      var input = box.querySelector("[data-key]"),
        correctButton = box.querySelector("[data-correct]"),
        incorrectButton = box.querySelector("[data-incorrect]"),
        saveButton = box.querySelector("[data-save]"),
        message = box.querySelector("[data-message]");
      input.value = entry.v;
      function correctionMode() {
        entry.v = input.value;
        entry.corrected =
          normalized(input.value) !== normalized(entry.originalValue);
        entry.confirmed = false;
        entry.reviewDecision = "incorrect";
        saveButton.hidden = false;
        correctButton.classList.remove("is-selected");
        incorrectButton.classList.add("is-selected");
        box.className = "field incorrect";
        message.textContent =
          "Enter the correct value, then select Save correction.";
        updateReviewStatus();
      }
      input.addEventListener("input", function () {
        if (normalized(input.value) !== normalized(entry.originalValue))
          correctionMode();
      });
      input.addEventListener("change", function () {
        if (normalized(input.value) !== normalized(entry.originalValue))
          correctionMode();
      });
      correctButton.addEventListener("click", function () {
        if (entry.learned) entry.originalValue = input.value;
        entry.v = input.value;
        entry.corrected = false;
        entry.confirmed = true;
        entry.reviewDecision = "correct";
        saveButton.hidden = true;
        correctButton.classList.add("is-selected");
        incorrectButton.classList.remove("is-selected");
        box.className = "field high confirmed correct";
        message.textContent = "Confirmed as correct.";
        updateReviewStatus();
      });
      incorrectButton.addEventListener("click", function () {
        entry.confirmed = false;
        entry.reviewDecision = "incorrect";
        saveButton.hidden = false;
        correctButton.classList.remove("is-selected");
        incorrectButton.classList.add("is-selected");
        box.className = "field incorrect";
        message.textContent =
          "Change the value above, then select Save correction.";
        input.focus();
        if (type === "text" && input.select) input.select();
        updateReviewStatus();
      });
      saveButton.addEventListener("click", function () {
        var correctedValue = clean(input.value);
        if (!correctedValue) {
          message.textContent = "Enter a corrected value before saving.";
          input.focus();
          return;
        }
        if (normalized(correctedValue) === normalized(entry.originalValue)) {
          message.textContent =
            "The value was not changed. Choose Correct instead.";
          return;
        }
        entry.v = correctedValue;
        entry.corrected = true;
        entry.confirmed = true;
        entry.reviewDecision = "incorrect-corrected";
        entry.c = 100;
        learnCorrection(kind, key, entry.originalValue, correctedValue, entry);
        renderLearningRules();
        saveButton.hidden = true;
        box.className = "field high confirmed corrected";
        message.textContent =
          AUTO_APPLY_FIELDS.indexOf(key) >= 0 && entry.originalValue
            ? "Correction saved. This mapping will be used on similar future documents."
            : "Correction saved as a reviewed example. Case-specific values will not be copied into another case.";
        updateReviewStatus();
      });
    });
    updateReviewStatus();
  }

  function updateReviewStatus() {
    var allFields = [];
    ["app", "ill"].forEach(function (kind) {
      if (docs[kind])
        Object.keys(docs[kind].fields).forEach(function (key) {
          allFields.push(docs[kind].fields[key]);
        });
    });
    var reviewed = allFields.filter(function (field) {
      return field.confirmed;
    }).length;
    var complete = allFields.length > 0 && reviewed === allFields.length;
    if ($("reviewProgressText"))
      $("reviewProgressText").textContent =
        reviewed + " of " + allFields.length + " fields reviewed";
    if ($("compareBtn")) $("compareBtn").disabled = !complete;
    if ($("runStatus") && complete) {
      $("runStatus").textContent = "Review complete — ready to compare";
      $("runStatus").className = "status good";
    }
  }

  if ($("state"))
    $("state").addEventListener("change", function () {
      var ny = $("state").value === "NY";
      if ($("ruleBadge")) {
        $("ruleBadge").textContent = ny
          ? "NY Reg 74 checks enabled"
          : "Standard rules";
        $("ruleBadge").className = "status " + (ny ? "warn" : "pending");
      }
    });
  if ($("extractBtn"))
    $("extractBtn").addEventListener("click", async function () {
      try {
        showError("");
        $("extractBtn").disabled = true;
        if ($("compareBtn")) $("compareBtn").disabled = true;
        if ($("runStatus")) {
          $("runStatus").textContent = "Extracting PDFs…";
          $("runStatus").className = "status warn";
        }
        var both = await Promise.all([
          extractPdf(files.app, "app"),
          extractPdf(files.ill, "ill"),
        ]);
        docs.app = both[0];
        docs.ill = both[1];
        if ($("appText")) $("appText").value = docs.app.text;
        if ($("illText")) $("illText").value = docs.ill.text;
        renderFields("app");
        renderFields("ill");
        if ($("runStatus")) {
          $("runStatus").textContent =
            "Extraction complete — mark each value Correct or Incorrect";
          $("runStatus").className = "status good";
        }
      } catch (error) {
        console.error(error);
        showError(
          "Extraction failed. Confirm both files are valid PDFs and the PDF/OCR libraries are allowed.",
        );
        if ($("runStatus")) {
          $("runStatus").textContent = "Extraction failed";
          $("runStatus").className = "status bad";
        }
      } finally {
        if ($("extractBtn")) $("extractBtn").disabled = false;
      }
    });

  function meaningful(value) {
    var n = normalized(value);
    return !!n && n !== "not found" && n !== "manual review";
  }
  function addRequirement(list, text, reason) {
    if (
      text &&
      !list.some(function (entry) {
        return entry.text === text;
      })
    )
      list.push({ text: text, reason: reason });
  }
  function parseDate(value) {
    var match = clean(value).match(/^(\d{1,2})\/(\d{1,2})\/(\d{2}|\d{4})$/);
    if (!match) return null;
    var month = Number(match[1]),
      day = Number(match[2]),
      year = Number(match[3]);
    if (year < 100) year += year >= 50 ? 1900 : 2000;
    var date = new Date(year, month - 1, day);
    return date.getFullYear() === year &&
      date.getMonth() === month - 1 &&
      date.getDate() === day
      ? date
      : null;
  }
  function compareDocuments() {
    if (!docs.app || !docs.ill) {
      showError("Please extract both PDFs before comparing them.");
      return;
    }
    var unreviewed = [];
    ["app", "ill"].forEach(function (kind) {
      Object.keys(docs[kind].fields).forEach(function (key) {
        if (!docs[kind].fields[key].confirmed)
          unreviewed.push(kind + ":" + key);
      });
    });
    if (unreviewed.length) {
      showError(
        "Please mark every extracted value Correct or Incorrect and save all corrections before comparing.",
      );
      return;
    }
    showError("");
    var app = docs.app.fields,
      ill = docs.ill.fields,
      state = $("state") ? $("state").value : "",
      rows = [],
      requirements = [];
    [
      "plan",
      "faceAmount",
      "apl",
      "loanRate",
      "dividend",
      "waiver",
      "gir",
      "frequency",
    ].forEach(function (key) {
      var appValue = app[key] ? app[key].v : "",
        illValue = ill[key] ? ill[key].v : "",
        result = "Review",
        note = "At least one value was not reliably extracted.";
      if (meaningful(appValue) && meaningful(illValue)) {
        if (normalized(appValue) === normalized(illValue)) {
          result = "Match";
          note = "Values match.";
        } else if (state === "NY" && key === "loanRate") {
          result = "Excluded";
          note = "Loan rate is excluded from the NY matching requirement.";
        } else {
          result = "Mismatch";
          note = "Values do not match.";
          if (typeof templates[key] === "function")
            addRequirement(
              requirements,
              templates[key](appValue, illValue),
              fieldLabel(key) + " mismatch",
            );
        }
      }
      rows.push({
        label: fieldLabel(key),
        app: appValue,
        ill: illValue,
        result: result,
        note: note,
      });
    });
    var appFrequency = app.frequency ? app.frequency.v : "",
      appBilling = app.billing ? app.billing.v : "";
    if (/annual/i.test(appFrequency) && /pre-authorized|pac/i.test(appBilling))
      addRequirement(
        requirements,
        templates.annualPac(),
        "Annual PAC combination",
      );
    if (state === "NY") {
      var signature = ill.signatureEvidence ? ill.signatureEvidence.v : "";
      if (normalized(signature) !== "signed")
        addRequirement(
          requirements,
          templates.nyUnsigned(),
          "NY illustration signature",
        );
      var appDate = parseDate(app.signedDate ? app.signedDate.v : ""),
        illDate = parseDate(ill.signedDate ? ill.signedDate.v : "");
      if (appDate && illDate && illDate > appDate)
        rows.push({
          label: "NY signature timing",
          app: app.signedDate.v,
          ill: ill.signedDate.v,
          result: "Mismatch",
          note: "Illustration signature date is after the Part 1 signature date.",
        });
      var dates = (ill.createdDates ? ill.createdDates.v : "")
        .split(",")
        .map(function (value) {
          return value.trim();
        })
        .filter(Boolean);
      if (dates.length > 1)
        rows.push({
          label: "NY created dates",
          app: "One consistent date required",
          ill: ill.createdDates.v,
          result: "Mismatch",
          note: "Multiple illustration created dates were extracted.",
        });
    } else if (
      normalized(app.lisr ? app.lisr.v : "") === "yes" &&
      normalized(ill.signatureEvidence ? ill.signatureEvidence.v : "") !==
        "unsigned"
    )
      addRequirement(
        requirements,
        templates.lisrUnsigned(),
        "LISR detected on Part 1",
      );
    renderResults(rows, requirements);
    if ($("runStatus")) {
      $("runStatus").textContent = "Comparison complete";
      $("runStatus").className = "status good";
    }
    if ($("results"))
      $("results").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderResults(rows, requirements) {
    var mismatch = rows.filter(function (r) {
      return r.result === "Mismatch";
    }).length;
    var match = rows.filter(function (r) {
      return r.result === "Match";
    }).length;
    var review = rows.filter(function (r) {
      return r.result === "Review";
    }).length;
    var excluded = rows.filter(function (r) {
      return r.result === "Excluded";
    }).length;
    if ($("summary"))
      $("summary").innerHTML =
        '<span class="status ' +
        (mismatch ? "bad" : "good") +
        '">' +
        mismatch +
        ' mismatches</span> <span class="status good">' +
        match +
        ' matches</span> <span class="status warn">' +
        review +
        " manual review</span>" +
        (excluded
          ? ' <span class="status pending">' + excluded + " excluded</span>"
          : "");
    if ($("results"))
      $("results").innerHTML =
        "<table><thead><tr><th>Field</th><th>Part 1</th><th>Illustration</th><th>Result</th><th>Note</th></tr></thead><tbody>" +
        rows
          .map(function (row) {
            var cls =
              row.result === "Match"
                ? "good"
                : row.result === "Mismatch"
                  ? "bad"
                  : "warn";
            return (
              "<tr><td>" +
              escapeHtml(row.label) +
              "</td><td>" +
              escapeHtml(row.app || "Not extracted") +
              "</td><td>" +
              escapeHtml(row.ill || "Not extracted") +
              '</td><td><span class="status ' +
              cls +
              '">' +
              escapeHtml(row.result) +
              "</span></td><td>" +
              escapeHtml(row.note) +
              "</td></tr>"
            );
          })
          .join("") +
        "</tbody></table>";
    var host = $("requirements");
    if (!host) return;
    if (!requirements.length) {
      host.className = "empty";
      host.textContent =
        "No exact requirements were generated. Confirm fields marked for manual review.";
      host.dataset.copy = "";
      if ($("copyAllBtn")) $("copyAllBtn").disabled = true;
      return;
    }
    host.className = "";
    host.innerHTML = requirements
      .map(function (requirement, index) {
        return (
          '<div class="req"><button type="button" class="btn secondary" data-copy="' +
          index +
          '">Copy</button><strong>' +
          escapeHtml(requirement.reason) +
          '</strong><div class="mono">' +
          escapeHtml(requirement.text) +
          "</div></div>"
        );
      })
      .join("");
    host.dataset.copy = requirements
      .map(function (requirement) {
        return requirement.text;
      })
      .join("\n\n");
    if ($("copyAllBtn")) $("copyAllBtn").disabled = false;
    host.querySelectorAll("[data-copy]").forEach(function (button) {
      button.addEventListener("click", function () {
        var requirement = requirements[Number(button.dataset.copy)];
        if (requirement && navigator.clipboard)
          navigator.clipboard.writeText(requirement.text);
      });
    });
  }

  function exportRules() {
    var rules = getLearningRules(),
      blob = new Blob(
        [
          JSON.stringify(
            {
              exportedAt: new Date().toISOString(),
              ruleCount: rules.length,
              rules: rules,
            },
            null,
            2,
          ),
        ],
        { type: "application/json" },
      );
    var url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download = "illustration-validator-learning-rules.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  if ($("compareBtn"))
    $("compareBtn").addEventListener("click", compareDocuments);
  if ($("copyAllBtn"))
    $("copyAllBtn").addEventListener("click", function () {
      var host = $("requirements"),
        text = host ? host.dataset.copy || "" : "";
      if (text && navigator.clipboard) navigator.clipboard.writeText(text);
    });
  if ($("resetBtn"))
    $("resetBtn").addEventListener("click", function () {
      window.location.reload();
    });
  if ($("exportLearningBtn"))
    $("exportLearningBtn").addEventListener("click", exportRules);
  if ($("clearLearningBtn"))
    $("clearLearningBtn").addEventListener("click", function () {
      if (window.confirm("Delete all saved correction rules?")) {
        localStorage.removeItem(LEARNING_KEY);
        renderLearningRules();
      }
    });

  window.IllustrationValidatorLearning = {
    getRules: getLearningRules,
    renderRules: renderLearningRules,
    deleteRule: deleteLearningRule,
    clearRules: function () {
      localStorage.removeItem(LEARNING_KEY);
      renderLearningRules();
    },
    exportRules: exportRules,
  };

  renderLearningRules();
  updateButtons();
})();
