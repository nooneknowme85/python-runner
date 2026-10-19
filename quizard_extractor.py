"""Quizard Extraction Engine for Python Runner.

Automates test discovery, question extraction, answer key parsing,
and syllabus extraction from Quizard, formatted for Quizzy.
Runs asynchronously in background threads with live log streaming.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import uuid
import zipfile

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.sync_api import sync_playwright

DEFAULT_BASE_URL = "https://quizard-v3-m-d396d1ad4209.herokuapp.com/"

EXTRACTION_JS = """
(args) => {
    const { testName, testId, batchPrefix, duration } = args;
    
    function isAdvTest(name) {
        if (!name) return false;
        const s = String(name);
        if (/mains/i.test(s) || /neet/i.test(s)) return false;
        if (/advance/i.test(s)) return true;
        if (/paper\s*[:\-_]?\s*[12]\b/i.test(s)) return true;
        return false;
    }
    const isAdv = isAdvTest(testName) || isAdvTest(batchPrefix);
    const advMarking = "📌 Pattern: JEE Advanced Marking Scheme\\n• Single MCQ: +3, -1\\n• Multi-Correct: +4 Full | Partial (+3, +2, +1) | -2 Penalty\\n• Numerical: +4, 0 (No negative marking)";
    const testDesc = isAdv ? `${batchPrefix} - ${testName}\\n\\n${advMarking}` : `${batchPrefix} - ${testName}`;

    // Quizzy 2026 File-Level Schema
    const data = {
        id: testId,
        name: testName,
        displayName: `${batchPrefix} - ${testName}`,
        description: testDesc,
        duration: duration,
        marking: { correct: 4, incorrect: -1 },
        exam_name: batchPrefix,
        sections: [],
        syllabus: "" 
    };

    // Helper: 100% MathJax Formula Cleaner
    function cleanHtmlMath(htmlString) {
        if (!htmlString) return "";
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = htmlString;
        
        const mathNodes = tempDiv.querySelectorAll('math');
        mathNodes.forEach(mathEl => {
            const annotation = mathEl.querySelector('annotation[encoding="LaTeX"]');
            if (annotation && annotation.textContent && annotation.textContent.trim()) {
                let latex = annotation.textContent
                    .replace(/&lt;/g, '<')
                    .replace(/&gt;/g, '>')
                    .replace(/&amp;/g, '&')
                    .replace(/&nbsp;/g, ' ')
                    .replace(/\\degree/g, '^{\\\\circ}')
                    .replace(/\\AA/g, '\\\\text{Å}')
                    .trim();
                const textNode = document.createTextNode(`$${latex}$`);
                mathEl.parentNode.replaceChild(textNode, mathEl);
            } else {
                let txt = (mathEl.textContent || "").trim();
                if (txt === '≤' || txt === '<=') txt = '\\\\le';
                else if (txt === '≥' || txt === '>=') txt = '\\\\ge';
                else if (txt === '≠' || txt === '!=') txt = '\\\\ne';
                else if (txt === '±' || txt === '+-') txt = '\\\\pm';
                else if (txt === '×') txt = '\\\\times';
                else if (txt === '÷') txt = '\\\\div';
                else if (txt === '°') txt = '^{\\\\circ}';
                const textNode = document.createTextNode(txt ? `$${txt}$` : "");
                mathEl.parentNode.replaceChild(textNode, mathEl);
            }
        });
        
        return tempDiv.innerHTML.trim();
    }

    // Helper: Strip all HTML tags for pure text comparison
    function getRawText(html) {
        if (!html) return "";
        let d = document.createElement('div');
        d.innerHTML = html;
        return d.innerText.replace(/\\s+/g, ' ').trim();
    }

    // 1. Scrape correct answers from the Results Table using DIRECT rows & cells
    // (Prevents nested matching tables inside question cells from corrupting row index)
    const tableAnswers = [];
    const mainTable = document.querySelector('#result table') || document.querySelector('table');
    if (mainTable) {
        const tbody = mainTable.querySelector('tbody') || mainTable;
        const topRows = Array.from(tbody.children).filter(el => el.tagName === 'TR');
        topRows.forEach(row => {
            const directTds = Array.from(row.children).filter(el => el.tagName === 'TD');
            if (directTds.length >= 3 && !directTds[0].innerText.includes('Total')) {
                const ansCell = directTds[2];
                const ansDivs = Array.from(ansCell.querySelectorAll('div'));
                const correctDiv = ansDivs.find(d => /Correct Answer/i.test(d.innerText));
                
                if (correctDiv) {
                    let clone = correctDiv.cloneNode(true);
                    let innerHTML = clone.innerHTML;
                    innerHTML = innerHTML.replace(/Correct Answers?:\\s*/i, '').trim();
                    tableAnswers.push(cleanHtmlMath(innerHTML));
                } else {
                    let cleaned = ansCell.innerHTML.replace(/Correct Answers?:\\s*/i, '').trim();
                    tableAnswers.push(cleanHtmlMath(cleaned));
                }
            }
        });
    }

    // 2. Build questions utilizing the local 'questions' or global 'window.questions' array
    const qSource = (typeof questions !== 'undefined' && Array.isArray(questions) && questions.length > 0)
        ? questions
        : (window.questions && window.questions.length > 0 ? window.questions : null);

    if (qSource && qSource.length > 0) {
        qSource.forEach((q, index) => {
            let secName = q.section || "Default";
            
            // Group questions by section name (handles interleaved sections like Botany/Zoology)
            let currentSection = data.sections.find(s => s.name === secName);
            if (!currentSection) {
                currentSection = { name: secName, icon: "", questions: [] };
                data.sections.push(currentSection);
            }
            let qNum = currentSection.questions.length + 1;

            // Schema-compliant types: Direct mapping from Quizard's native q.type
            let rawQType = (q.type || "").toLowerCase().trim();
            let qType = "mcq";
            if (rawQType === "multi" || rawQType === "multiple" || rawQType === "multimcq" || rawQType === "multi_mcq") {
                qType = "multi_mcq";
            } else if (rawQType === "integer" || rawQType === "numerical" || rawQType === "numeric" || rawQType === "integer_type") {
                qType = "integer";
            } else {
                qType = "mcq";
            }
            
            // Clean Question HTML & Math formulas
            let cleanQuestionHtml = cleanHtmlMath(q.question || "");
            
            // Extract all diagram images and preserve secondary figures
            let tempDiv = document.createElement('div');
            tempDiv.innerHTML = cleanQuestionHtml;
            let imgEls = Array.from(tempDiv.querySelectorAll('img'));
            let allImages = imgEls.map(img => img.src || img.getAttribute('src')).filter(Boolean);
            let primaryImageUrl = allImages.length > 0 ? allImages[0] : null;

            // Remove standalone question images from HTML body (so they render cleanly via image_url/images)
            imgEls.forEach(img => {
                const p = img.parentElement;
                if (p && (p.children.length === 1 || p.innerText.trim() === '')) {
                    img.remove();
                }
            });
            cleanQuestionHtml = tempDiv.innerHTML.trim();

            // Proceed if text OR image exists
            if (cleanQuestionHtml.length > 0 || primaryImageUrl) {
                let tableAns = tableAnswers[index] || "";
                let rawAnsText = getRawText(tableAns).trim();
                let upperAns = rawAnsText.toUpperCase();

                // Special case overrides from answer key
                let isBonus = false;
                if (upperAns === "ALL" || upperAns.includes("BONUS") || upperAns.includes("ALL OPTIONS")) {
                    qType = "multi_mcq";
                    isBonus = true;
                } else if (qType === "mcq" && (rawAnsText.includes(',') || (Array.from(new Set(upperAns.match(/[A-E]/g) || [])).length > 1))) {
                    // Safety: In case Quizard mislabeled a multi-choice question as single mcq
                    qType = "multi_mcq";
                } else if (qType === "mcq" && /^-?\d+(\.\d+)?$/.test(upperAns) && (!q.options || q.options.length === 0)) {
                    // Safety: In case Quizard mislabeled an integer question as single mcq
                    qType = "integer";
                }

                let questionData = {
                    id: `${testId}_${secName.substring(0,3).toLowerCase()}_q${qNum}`,
                    type: qType,
                    question: cleanQuestionHtml,
                    image_url: primaryImageUrl,
                    images: allImages.length > 1 ? allImages : (primaryImageUrl ? [primaryImageUrl] : [])
                };

                if (qType === "integer") {
                    questionData.answer = rawAnsText.replace(/[^0-9.\-]/g, '') || rawAnsText;
                    questionData.correct = null;
                    questionData.options = null;
                } else {
                    // Clean options, stripping wrapping <p>...</p> tags
                    let cleanOptions = (q.options || []).map(opt => {
                        let cleaned = cleanHtmlMath(opt);
                        cleaned = cleaned.replace(/^<p\s*[^>]*>/i, '').replace(/<\/p>$/i, '').trim();
                        return cleaned;
                    });
                    questionData.options = cleanOptions.length > 0 ? cleanOptions : ["A", "B", "C", "D"];
                    questionData.answer = null;
                    
                    if (isBonus) {
                        questionData.type = "multi_mcq";
                        questionData.is_bonus = true;
                        questionData.correct = questionData.options.map((_, i) => i);
                    } else if (qType === "multi_mcq") {
                        let matchedIndices = [];
                        const optLetters = ['A', 'B', 'C', 'D', 'E'];
                        let letterMatches = Array.from(new Set(upperAns.match(/[A-E]/g) || []));
                        if (letterMatches && letterMatches.length > 0 && questionData.options.length <= 5) {
                            matchedIndices = letterMatches
                                .map(l => optLetters.indexOf(l))
                                .filter(idx => idx >= 0 && idx < questionData.options.length);
                        }
                        if (matchedIndices.length === 0) {
                            questionData.options.forEach((opt, oIdx) => {
                                let optText = getRawText(opt);
                                if (optText && rawAnsText.includes(optText)) {
                                    matchedIndices.push(oIdx);
                                }
                            });
                        }
                        questionData.correct = matchedIndices.length > 0 ? Array.from(new Set(matchedIndices)) : [0];
                    } else {
                        // Standard MCQ resolution with letter fallback
                        let correctIndex = questionData.options.findIndex(opt => getRawText(opt) === rawAnsText);
                        
                        // Letter fallback if table answer is just A, B, C, D
                        if (correctIndex === -1 && /^[A-D]$/i.test(rawAnsText)) {
                            correctIndex = { 'A': 0, 'B': 1, 'C': 2, 'D': 3 }[rawAnsText.toUpperCase()] ?? -1;
                        }
                        // Substring fallback
                        if (correctIndex === -1) {
                            correctIndex = questionData.options.findIndex(opt => {
                                let optT = getRawText(opt);
                                return optT && (rawAnsText.includes(optT) || optT.includes(rawAnsText));
                            });
                        }
                        questionData.correct = correctIndex !== -1 ? correctIndex : 0;
                    }
                }

                // Add solution / explanation if present, and prepend bonus instruction if bonus
                const bonusNotice = "⚠️ **Bonus Question / All Options Correct**\\n*This question was officially awarded as BONUS. Please manually add the positive marks (+4) to your total obtained marks if you did not select all options.*";
                let officialExp = (q.solution || q.explanation) ? cleanHtmlMath(q.solution || q.explanation) : "";

                if (isBonus) {
                    questionData.is_bonus = true;
                    questionData.explanation = officialExp ? `${bonusNotice}\\n\\n---\\n${officialExp}` : bonusNotice;
                } else if (officialExp) {
                    questionData.explanation = officialExp;
                }

                currentSection.questions.push(questionData);
                qNum++;
            }
        });
    } else {
        // Fallback: Legacy table TR scraping with direct child cells and formula cleaner
        let currentSectionObj = null;
        let currentSecKey = "";
        let qNum = 1;
        const optMap = { 'A': 0, 'B': 1, 'C': 2, 'D': 3 };
        
        const mainTable = document.querySelector('#result table') || document.querySelector('table');
        const rows = mainTable ? Array.from((mainTable.querySelector('tbody') || mainTable).children).filter(el => el.tagName === 'TR') : [];
        
        rows.forEach(row => {
            const tds = Array.from(row.children).filter(el => el.tagName === 'TD');
            if (tds.length >= 3) {
                const qNumberStr = tds[0].innerText.trim();
                const img = tds[1].querySelector("img");
                const imageUrl = img ? (img.src || img.getAttribute('src')) : null;
                const cleanQText = cleanHtmlMath(tds[1].innerHTML);
                
                if (!isNaN(parseInt(qNumberStr))) {
                    if (!currentSectionObj) {
                        currentSectionObj = { name: "Default Section", icon: "", questions: [] };
                        currentSecKey = "def";
                        data.sections.push(currentSectionObj);
                    }

                    let correctAns = 0;
                    let answerVal = null;
                    let qType = "mcq";
                    const divs = tds[2].querySelectorAll("div");
                    let answerText = "";
                    divs.forEach(div => {
                        if (div.innerText.includes("Correct Answer")) answerText = div.innerText;
                    });
                    if (answerText) {
                        const match = answerText.match(/Correct Answers?\s*:\s*(.*)/i);
                        if (match) {
                            let rawAns = match[1].trim();
                            let upperAns = rawAns.toUpperCase();

                            let isBonus = false;
                            // 1. ALL / BONUS
                            if (upperAns === "ALL" || upperAns.includes("BONUS") || upperAns.includes("ALL OPTIONS")) {
                                qType = "multi_mcq";
                                correctAns = [0, 1, 2, 3];
                                isBonus = true;
                            }
                            // 2. INTEGER / NUMERICAL
                            else if (/^-?\d+(\.\d+)?$/.test(upperAns)) {
                                qType = "integer";
                                answerVal = upperAns;
                                correctAns = null;
                            }
                            // 3. MULTI-MCQ (only when comma-separated or 2+ distinct letters)
                            else {
                                let letterParts = Array.from(new Set(upperAns.match(/[A-D]/g) || []));
                                if (rawAns.includes(',') || letterParts.length > 1) {
                                    qType = "multi_mcq";
                                    correctAns = letterParts.map(s => optMap[s]).filter(x => x !== undefined);
                                    if (correctAns.length === 0) correctAns = [0];
                                } else {
                                    qType = "mcq";
                                    correctAns = letterParts.length === 1 && optMap[letterParts[0]] !== undefined 
                                        ? optMap[letterParts[0]] 
                                        : (optMap[upperAns] !== undefined ? optMap[upperAns] : 0);
                                }
                            }
                        }
                    }
                    let questionData = {
                        id: `${testId}_${currentSecKey}_q${qNum}`,
                        type: qType,
                        question: cleanQText,
                        image_url: imageUrl,
                        images: imageUrl ? [imageUrl] : [],
                        correct: correctAns
                    };
                    if (isBonus) {
                        questionData.is_bonus = true;
                        questionData.explanation = "⚠️ **Bonus Question / All Options Correct**\\n*This question was officially awarded as BONUS. Please manually add the positive marks (+4) to your total obtained marks if you did not select all options.*";
                    }
                    if (qType === "integer") {
                        questionData.answer = answerVal;
                        questionData.correct = null;
                        questionData.options = null;
                    } else {
                        questionData.options = ["A", "B", "C", "D"];
                        questionData.answer = null;
                    }
                    currentSectionObj.questions.push(questionData);
                    qNum++;
                }
            }
        });
    }

    // 3. Fallback: Extract Syllabus from Instructions modal 
    const instructionsContent = document.getElementById('instructionsContent');
    if (instructionsContent) {
        data.syllabus = instructionsContent.textContent.trim();
    } else {
        data.syllabus = "Syllabus not available";
    }

    return data;
}
"""


def generate_id_slug(prefix: str, raw_name: str) -> str:
    clean_name = re.sub(r'[^a-z0-9\s]', ' ', raw_name.lower())
    clean_name = re.sub(r'\s+', '_', clean_name).strip('_')
    return f"{prefix}_{clean_name}"


def sanitize_filename(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_\-\.\(\) ]', '_', name)


def clean_html_to_text(html_content: str) -> str:
    if not html_content:
        return "Syllabus not available"
    
    text = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', html_content, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(div|p|ul|ol|li|h[1-6])>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<li>', '• ', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    text = text.strip()

    match = re.search(r'General Instructions\s*(.*?)\s*Test Instructions', text, re.IGNORECASE | re.DOTALL)
    if match:
        extracted = match.group(1).strip()
        if extracted:
            return extracted
            
    return text


def format_quizzy_syllabus(raw_syllabus: str, test_name: str) -> dict:
    """Formats raw syllabus text into Quizzy's structured syllabus object."""
    if not raw_syllabus or not raw_syllabus.strip() or raw_syllabus.strip().lower() == "syllabus not available":
        return {
            "enabled": False,
            "type": "text",
            "title": f"{test_name} Syllabus",
            "buttonLabel": "Syllabus",
            "content": "Syllabus not available"
        }
    
    text = raw_syllabus.strip()
    if "📌" in text:
        return {
            "enabled": True,
            "type": "text",
            "title": f"{test_name} Syllabus",
            "buttonLabel": "Syllabus",
            "content": text
        }
    
    subj_pattern = re.compile(r'(?i)(?:^|\n)\s*(?:•\s*)?(Physics|Chemistry|Botany|Zoology|Mathematics|Biology)\s*[:\-]?\s*')
    if subj_pattern.search(text):
        parts = subj_pattern.split(text)
        subjects: Dict[str, List[str]] = {}
        for i in range(1, len(parts), 2):
            sname = parts[i].capitalize()
            scontent = parts[i + 1].strip()
            clines = [l.strip().lstrip('•*- ').strip() for l in scontent.split('\n') if l.strip().lstrip('•*- ').strip()]
            if clines:
                subjects[sname] = clines
        
        if subjects:
            formatted_blocks = []
            for sname, items in subjects.items():
                bullets = [f"• {it}" for it in items]
                formatted_blocks.append(f"📌 {sname.upper()}\n" + "\n".join(bullets))
            text = "\n\n".join(formatted_blocks)

    return {
        "enabled": True,
        "type": "text",
        "title": f"{test_name} Syllabus",
        "buttonLabel": "Syllabus",
        "content": text
    }


def fetch_syllabus(page, base_url: str, batch_id: str, batch_name: str, test_id: str) -> str:
    try:
        encoded_batch_name = urllib.parse.quote(batch_name)
        api_url = f"{base_url.rstrip('/')}/instructions/{batch_id}/{encoded_batch_name}/{test_id}/batch_test"
        response = page.request.get(api_url, timeout=10000)
        
        if response.status == 200:
            res_json = response.json()
            raw_html = res_json.get("instructions_html", "")
            return clean_html_to_text(raw_html)
    except Exception:
        pass
    return "Syllabus not available"


class QuizardBrain:
    """Intelligent Cache & Local Library Indexer for Quizard Tests.
    
    Scans existing downloaded batch archives (e.g. from 'my-original-file/pw test'
    and runner workspace outputs) to keep track of already downloaded tests.
    Enables instant detection of which tests are Already Downloaded vs New Tests.
    """

    def __init__(self, pw_test_dir: Optional[Path] = None, cache_file: Optional[Path] = None):
        self.pw_test_dir = pw_test_dir or (Path(__file__).parent.parent / "my-original-file" / "pw test")
        self.runner_dir = Path(__file__).parent / "workspace" / "quizard_outputs"
        self.cache_file = cache_file or (Path(__file__).parent / "workspace" / "quizard_brain.json")
        
        # In-memory structures
        self.total_tests: int = 0
        self.total_batches: int = 0
        self.last_scanned: str = ""
        # norm_batch -> set of norm_test_names
        self.batches: Dict[str, Dict[str, Any]] = {}
        # global set of norm_test_names
        self.global_tests: set = set()

        self.load_or_scan()

    @staticmethod
    def norm(s: str) -> str:
        """Normalizes titles for resilient matching across minor formatting differences."""
        if not s:
            return ""
        clean = (s or "").lower()
        clean = re.sub(r'_\d+$', '', clean)
        clean = re.sub(r'[^a-z0-9]', '', clean)
        return clean

    def load_or_scan(self):
        """Loads cached brain index if valid; otherwise performs a rapid local scan."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.total_tests = data.get("total_tests", 0)
                self.total_batches = data.get("total_batches", 0)
                self.last_scanned = data.get("last_scanned", "")
                self.batches = {}
                self.global_tests = set()

                for nb, binfo in data.get("batches", {}).items():
                    test_set = set(binfo.get("tests", []))
                    self.batches[nb] = {
                        "name": binfo.get("name", nb),
                        "tests": test_set,
                        "count": len(test_set)
                    }
                    self.global_tests.update(test_set)
                if self.total_tests > 0:
                    return
            except Exception:
                pass

        self.scan_library()

    def scan_library(self) -> Dict[str, Any]:
        """Scans all zip files in pw test directory and runner workspace."""
        self.batches = {}
        self.global_tests = set()
        total_count = 0

        target_dirs = []
        if self.pw_test_dir.exists():
            target_dirs.append(self.pw_test_dir)
        if self.runner_dir.exists():
            target_dirs.append(self.runner_dir)

        for base_dir in target_dirs:
            for root, _, files in os.walk(base_dir):
                for f in files:
                    if f.lower().endswith(".zip"):
                        zp = os.path.join(root, f)
                        bname = os.path.splitext(f)[0]
                        norm_b = self.norm(bname)
                        if norm_b not in self.batches:
                            self.batches[norm_b] = {
                                "name": bname,
                                "tests": set(),
                                "count": 0
                            }
                        try:
                            with zipfile.ZipFile(zp) as z:
                                for member in z.namelist():
                                    if member.lower().endswith(".json"):
                                        tname = os.path.splitext(os.path.basename(member))[0]
                                        norm_t = self.norm(tname)
                                        if norm_t:
                                            self.batches[norm_b]["tests"].add(norm_t)
                                            self.global_tests.add(norm_t)
                                            total_count += 1
                        except Exception:
                            pass
                    elif f.lower().endswith(".json") and not f.startswith("quizard_brain"):
                        parent_batch = os.path.basename(root)
                        norm_b = self.norm(parent_batch)
                        if norm_b not in self.batches:
                            self.batches[norm_b] = {"name": parent_batch, "tests": set(), "count": 0}
                        tname = os.path.splitext(f)[0]
                        norm_t = self.norm(tname)
                        if norm_t:
                            self.batches[norm_b]["tests"].add(norm_t)
                            self.global_tests.add(norm_t)
                            total_count += 1

        for binfo in self.batches.values():
            binfo["count"] = len(binfo["tests"])

        self.total_tests = total_count
        self.total_batches = len(self.batches)
        self.last_scanned = datetime.datetime.now().isoformat()

        self._save_cache()
        return self.get_stats()

    def _save_cache(self):
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            serializable = {
                "version": 1,
                "last_scanned": self.last_scanned,
                "total_tests": self.total_tests,
                "total_batches": self.total_batches,
                "batches": {
                    nb: {
                        "name": binfo["name"],
                        "tests": list(binfo["tests"]),
                        "count": binfo["count"]
                    }
                    for nb, binfo in self.batches.items()
                }
            }
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(serializable, f, indent=2)
        except Exception:
            pass

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_tests": self.total_tests,
            "total_batches": self.total_batches,
            "last_scanned": self.last_scanned,
            "pw_test_dir": str(self.pw_test_dir),
            "exists": self.pw_test_dir.exists()
        }

    def is_downloaded(self, batch_name: str, test_title: str) -> bool:
        """Determines if a test is already present in our local library."""
        norm_t = self.norm(test_title)
        if not norm_t:
            return False

        norm_b = self.norm(batch_name)

        if norm_b in self.batches:
            if norm_t in self.batches[norm_b]["tests"]:
                return True

        for nb, binfo in self.batches.items():
            if norm_b in nb or nb in norm_b:
                if norm_t in binfo["tests"]:
                    return True

        return False

    def check_batch_tests(self, batch_name: str, candidate_titles: List[str]) -> Dict[str, Any]:
        """Separates candidate tests into already downloaded vs new tests."""
        downloaded = []
        new_tests = []

        for title in candidate_titles:
            if self.is_downloaded(batch_name, title):
                downloaded.append(title)
            else:
                new_tests.append(title)

        return {
            "batch_name": batch_name,
            "total": len(candidate_titles),
            "downloaded": downloaded,
            "downloaded_count": len(downloaded),
            "new_tests": new_tests,
            "new_tests_count": len(new_tests)
        }

    def record_downloaded(self, batch_name: str, test_title: str):
        """Dynamically registers newly downloaded test into brain."""
        norm_b = self.norm(batch_name)
        norm_t = self.norm(test_title)
        if not norm_t:
            return
        if norm_b not in self.batches:
            self.batches[norm_b] = {"name": batch_name, "tests": set(), "count": 0}
        self.batches[norm_b]["tests"].add(norm_t)
        self.batches[norm_b]["count"] = len(self.batches[norm_b]["tests"])
        self.global_tests.add(norm_t)
        self.total_tests += 1


# Global Brain Singleton
brain = QuizardBrain()


class QuizardJob:
    """Thread-safe background runner for Quizard batch extraction."""

    def __init__(
        self,
        category: str,
        batches: str = "all",
        base_url: str = DEFAULT_BASE_URL,
        use_api_syllabus: bool = True,
        headless: bool = True,
        download_mode: str = "new_only",
        output_dir: Optional[Path] = None,
    ):
        self.id = uuid.uuid4().hex[:10]
        self.base_url = (base_url or DEFAULT_BASE_URL).strip()
        self.category = category.strip()
        self.batch_input = (batches or "all").strip()
        self.use_api_syllabus = use_api_syllabus
        self.headless = headless
        self.download_mode = (download_mode or "new_only").strip().lower()

        self.brain = brain
        self.test_details: List[Dict[str, Any]] = []
        self.text_format_count: int = 0
        self.image_only_count: int = 0
        self.skipped_brain_list: List[Dict[str, Any]] = []

        self.output_dir = output_dir or (Path(__file__).parent / "workspace" / "quizard_outputs")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.state = "queued"  # queued, running, finished, stopped, error
        self.message = "Job queued..."
        self.done = 0
        self.total = 1
        self.active_batch = ""
        self.active_test = ""
        self.stop_requested = False

        self.logs: List[Dict[str, str]] = []
        self.failed_tracker: Dict[str, List[str]] = {}
        self.skipped_tracker: Dict[str, List[str]] = {}
        self.skipped_list: List[Dict[str, Any]] = []
        self.duplicate_tracker: Dict[str, List[Dict[str, Any]]] = {}
        self.duplicate_list: List[Dict[str, Any]] = []
        self.zip_files: List[Dict[str, Any]] = []
        self.json_files: List[Dict[str, Any]] = []
        self.summary: Dict[str, Any] = {}
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None

    def log(self, msg: str, level: str = "info"):
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        entry = {"time": now_str, "level": level, "msg": msg}
        self.logs.append(entry)
        if len(self.logs) > 1000:
            self.logs = self.logs[-1000:]
        print(f"[{now_str}] [{level.upper()}] {msg}")

    def progress(self, done: int, total: int, msg: str, active_batch: str = "", active_test: str = ""):
        self.done = done
        self.total = max(total, 1)
        self.message = msg
        if active_batch:
            self.active_batch = active_batch
        if active_test:
            self.active_test = active_test

    def stop(self):
        self.stop_requested = True
        self.log("⏹️ Stop requested by user. Terminating process...", level="warning")
        self.message = "Stopping job..."

    def run(self):
        self.state = "running"
        self.start_time = time.time()
        self.log(f"🚀 Starting Quizard extraction job [{self.id}]")
        self.log(f"Category: '{self.category}' | Batches: '{self.batch_input}' | Headless: {self.headless}")
        self.progress(0, 10, "Launching Playwright browser session...")

        total_saved_count = 0
        total_skipped_count = 0
        total_duplicates_count = 0
        total_tests_processed = 0

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context()
                page = context.new_page()

                self.log(f"🌐 Navigating to Quizard at {self.base_url} ...")
                self.progress(1, 10, f"Navigating to {self.base_url}...")
                page.goto(self.base_url)

                if self.stop_requested:
                    browser.close()
                    self._finish_stopped()
                    return

                batches_to_process: List[str] = []

                # Check if category itself is a direct batch URL
                is_cat_url = bool(re.match(r'^https?://', self.category.strip(), re.IGNORECASE) or '/batch/' in self.category)
                if is_cat_url:
                    batches_to_process = [self.category.strip()]
                    self.log(f"🔗 Detected direct batch URL in category: {self.category.strip()}", level="success")
                elif self.batch_input.lower() == 'all':
                    self.log(f"🔍 Discovering all batches under category '{self.category}'...")
                    self.progress(2, 10, f"Finding batches for '{self.category}'...")
                    
                    cat_loc = page.get_by_text(self.category, exact=True).first
                    cat_loc.click()
                    page.wait_for_timeout(2000)

                    page.evaluate("""() => {
                        const scrollables = Array.from(document.querySelectorAll('*')).filter(el => {
                            return el.scrollHeight > el.clientHeight && window.getComputedStyle(el).overflowY !== 'visible';
                        });
                        scrollables.forEach(s => { s.scrollTop = s.scrollHeight; });
                    }""")
                    page.wait_for_timeout(1500)

                    batches_to_process = page.evaluate('''(catName) => {
                        const headers = Array.from(document.querySelectorAll('*')).filter(el => el.innerText && el.innerText.trim() === catName);
                        if (headers.length === 0) return [];
                        const activeHeader = headers[headers.length - 1]; 
                        
                        let container = activeHeader.parentElement;
                        while(container && container.clientHeight === container.scrollHeight) {
                            container = container.parentElement;
                            if(!container) break;
                        }
                        if(!container) container = document.body;
                        
                        const elements = Array.from(container.querySelectorAll('*')).filter(el => {
                            return el.children.length === 0 && el.innerText && el.innerText.trim().length > 3;
                        });
                        const exclude = ['For JEE', 'For NEET', catName, 'Start Test'];
                        const results = elements.map(e => e.innerText.trim()).filter(text => !exclude.includes(text));
                        return [...new Set(results)]; 
                    }''', self.category)

                    if not batches_to_process:
                        self.log("❌ Failed to automatically find any batches under that category.", level="error")
                        browser.close()
                        self.state = "error"
                        self.message = f"No batches discovered for category '{self.category}'"
                        return

                    self.log(f"📊 Discovered {len(batches_to_process)} batches: {', '.join(batches_to_process)}", level="success")
                else:
                    batches_to_process = [b.strip() for b in self.batch_input.split(',') if b.strip()]
                    self.log(f"📋 Queued {len(batches_to_process)} specific batches: {', '.join(batches_to_process)}")

                batch_total = len(batches_to_process)

                for b_idx, current_batch in enumerate(batches_to_process, 1):
                    if self.stop_requested:
                        break

                    # Check if this batch item is a direct URL
                    is_batch_url = bool(re.match(r'^https?://', current_batch.strip(), re.IGNORECASE) or '/batch/' in current_batch)
                    direct_url = current_batch.strip() if is_batch_url else None
                    
                    if direct_url:
                        url_match = re.search(r'/batch/[^/]+/([^/]+)/', direct_url)
                        if url_match:
                            display_batch_name = urllib.parse.unquote(url_match.group(1))
                        else:
                            display_batch_name = direct_url.rstrip('/').split('/')[-1]
                    else:
                        display_batch_name = current_batch

                    self.active_batch = display_batch_name
                    self.log(f"▶️ STARTING BATCH [{b_idx}/{batch_total}]: '{display_batch_name}'", level="batch")
                    self.progress(b_idx - 1, batch_total, f"Batch {b_idx}/{batch_total}: {display_batch_name}", active_batch=display_batch_name)

                    current_id_prefix = re.sub(r'[^a-z0-9]', '_', display_batch_name.lower())
                    current_id_prefix = re.sub(r'_+', '_', current_id_prefix).strip('_')

                    safe_batch_name = sanitize_filename(display_batch_name)
                    batch_dir = self.output_dir / safe_batch_name
                    batch_dir.mkdir(parents=True, exist_ok=True)

                    try:
                        if direct_url:
                            self.log(f"🌐 Navigating directly to batch URL: {direct_url}")
                            page.goto(direct_url, timeout=60000)
                            page.wait_for_load_state("networkidle")
                            batch_url = page.url
                        else:
                            page.goto(self.base_url)
                            page.wait_for_load_state("networkidle")
                            page.get_by_text(self.category, exact=True).first.click()
                            page.wait_for_timeout(1000)

                            batch_locator = page.get_by_text(current_batch, exact=True).first
                            href = batch_locator.get_attribute("href")
                            if href:
                                batch_url = urllib.parse.urljoin(page.url, href)
                                page.goto(batch_url)
                                page.wait_for_load_state("networkidle")
                            else:
                                batch_locator.scroll_into_view_if_needed()
                                page.wait_for_timeout(300)
                                batch_locator.evaluate("el => el.click()")
                                page.wait_for_load_state("networkidle")
                                batch_url = page.url

                        test_cards_data = page.evaluate("""() => {
                            const cards = Array.from(document.querySelectorAll('.test-card'));
                            if (cards.length > 0) {
                                return cards.map(card => {
                                    const titleEl = card.querySelector('.test-card-title');
                                    const instrBtn = card.querySelector('.btn-instructions');
                                    let batchId = null;
                                    let testId = null;
                                    
                                    const clickAttr = instrBtn ? instrBtn.getAttribute('onclick') : "";
                                    const match = clickAttr.match(/['"]([a-f0-9]{24})['"]\\s*,\\s*['"]([a-f0-9]{24})['"]/i);
                                    if (match) {
                                        batchId = match[1];
                                        testId = match[2];
                                    }
                                    
                                    return {
                                        title: titleEl ? titleEl.innerText.trim() : "Unknown_Test",
                                        batchId: batchId,
                                        testId: testId
                                    };
                                });
                            } else {
                                const btns = Array.from(document.querySelectorAll('button')).filter(b => b.innerText.trim().match(/Start Test/i));
                                return btns.map((btn, index) => {
                                    let parent = btn.parentElement;
                                    let title = `Test_${index+1}`;
                                    while (parent) {
                                        const text = parent.innerText;
                                        if (text && text.includes('Questions') && text.includes('Start Test')) {
                                            const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
                                            title = lines[0]; 
                                            break;
                                        }
                                        parent = parent.parentElement;
                                    }
                                    return { title: title, batchId: null, testId: null };
                                });
                            }
                        }""")

                        start_buttons = page.locator('button:has-text("Start Test")')
                        test_count = start_buttons.count()
                        self.log(f"📋 Found {test_count} tests in batch '{current_batch}'.")

                        batch_seen_titles: Dict[str, int] = {}

                        for i in range(test_count):
                            if self.stop_requested:
                                break

                            tdata = test_cards_data[i] if i < len(test_cards_data) else {"title": f"Test_{i+1}", "batchId": None, "testId": None}
                            raw_test_name = tdata["title"]
                            batch_id = tdata["batchId"]
                            internal_test_id = tdata["testId"]
                            self.active_test = raw_test_name
                            total_tests_processed += 1

                            # Brain Cache Check: Skip already downloaded tests if download_mode is 'new_only'
                            if self.download_mode == "new_only" and self.brain.is_downloaded(current_batch, raw_test_name):
                                total_skipped_count += 1
                                self.log(f"   🧠 [Brain Cache] '{raw_test_name}' is already downloaded in library. (Skipping {i + 1}/{test_count})", level="info")
                                self.skipped_brain_list.append({
                                    "batch": current_batch,
                                    "test": raw_test_name,
                                    "reason": "Already downloaded in local library"
                                })
                                continue

                            base_clean_name = sanitize_filename(raw_test_name)
                            is_duplicate = False
                            dup_index = 1

                            # Handle tests with same name: DO NOT skip! Extract both!
                            if raw_test_name in batch_seen_titles:
                                batch_seen_titles[raw_test_name] += 1
                                dup_index = batch_seen_titles[raw_test_name]
                                file_name = f"{base_clean_name}_{dup_index}.json"
                                while (batch_dir / file_name).exists():
                                    dup_index += 1
                                    file_name = f"{base_clean_name}_{dup_index}.json"
                                is_duplicate = True
                            elif (batch_dir / f"{base_clean_name}.json").exists():
                                batch_seen_titles[raw_test_name] = 2
                                dup_index = 2
                                file_name = f"{base_clean_name}_{dup_index}.json"
                                while (batch_dir / file_name).exists():
                                    dup_index += 1
                                    file_name = f"{base_clean_name}_{dup_index}.json"
                                is_duplicate = True
                            else:
                                batch_seen_titles[raw_test_name] = 1
                                file_name = f"{base_clean_name}.json"

                            json_path = batch_dir / file_name

                            if is_duplicate:
                                total_duplicates_count += 1
                                self.log(f"   📑 Duplicate test name: '{raw_test_name}' in batch '{current_batch}'. Extracting BOTH -> saving as '{file_name}' ({i + 1}/{test_count})", level="warning")
                                if current_batch not in self.duplicate_tracker:
                                    self.duplicate_tracker[current_batch] = []
                                self.duplicate_tracker[current_batch].append({
                                    "test": raw_test_name,
                                    "saved_as": file_name,
                                    "copy_index": dup_index
                                })
                                self.duplicate_list.append({
                                    "batch": current_batch,
                                    "test": raw_test_name,
                                    "saved_as": file_name,
                                    "filename": file_name,
                                    "rel_path": f"{safe_batch_name}/{file_name}",
                                    "reason": f"Duplicate test name in same batch (saved as {file_name})"
                                })

                            max_attempts = 3
                            json_saved = False
                            attempt = 0

                            while attempt < max_attempts and not json_saved:
                                if self.stop_requested:
                                    break
                                attempt += 1
                                try:
                                    self.log(f"   ⚙️ Processing Test: {raw_test_name} ({i + 1}/{test_count})" + (f" [Duplicate Copy #{dup_index}]" if is_duplicate else ""))
                                    self.progress(
                                        b_idx - 1,
                                        batch_total,
                                        f"[{b_idx}/{batch_total}] Test {i + 1}/{test_count}: {raw_test_name}" + (f" (#{dup_index})" if is_duplicate else ""),
                                        active_batch=current_batch,
                                        active_test=raw_test_name
                                    )

                                    syllabus_text = None
                                    if self.use_api_syllabus:
                                        if batch_id and internal_test_id:
                                            syllabus_text = fetch_syllabus(page, self.base_url, batch_id, display_batch_name, internal_test_id)
                                        else:
                                            syllabus_text = "Syllabus not available (No IDs found on page)"

                                    test_slug_name = f"{raw_test_name}_{dup_index}" if is_duplicate else raw_test_name
                                    test_id = generate_id_slug(current_id_prefix, test_slug_name)
                                    duration = 60 if "short" in raw_test_name.lower() else 180

                                    buttons = page.locator('button:has-text("Start Test")')
                                    buttons.nth(i).scroll_into_view_if_needed()
                                    page.wait_for_timeout(300)
                                    buttons.nth(i).click(force=True)

                                    start_quiz_btn = page.locator("text=/Start Quiz/i").first
                                    start_quiz_btn.wait_for(state="visible", timeout=20000)
                                    start_quiz_btn.scroll_into_view_if_needed()
                                    page.wait_for_timeout(1000)
                                    start_quiz_btn.click(force=True)

                                    page.wait_for_load_state("domcontentloaded")

                                    submit_btn = page.locator("text=/Submit/i").first
                                    submit_btn.wait_for(state="visible", timeout=20000)
                                    submit_btn.scroll_into_view_if_needed()
                                    page.wait_for_timeout(500)
                                    submit_btn.click(force=True)

                                    page.wait_for_timeout(1000)
                                    confirm_btn = page.locator("text=/Confirm/i").first
                                    if confirm_btn.count() > 0:
                                        confirm_btn.click(force=True)

                                    page.wait_for_selector('#result table, h3:has-text("Section")', timeout=25000)
                                    page.wait_for_timeout(1000)

                                    js_args = {
                                        "testName": raw_test_name,
                                        "testId": test_id,
                                        "batchPrefix": display_batch_name,
                                        "duration": duration
                                    }
                                    extracted_data = page.evaluate(EXTRACTION_JS, js_args)

                                    if self.use_api_syllabus and syllabus_text is not None:
                                        extracted_data["syllabus"] = syllabus_text

                                    # Format syllabus and clean sections for Quizzy website
                                    raw_syl = extracted_data.get("syllabus", "")
                                    extracted_data["syllabus"] = format_quizzy_syllabus(raw_syl, raw_test_name)
                                    extracted_data["sections"] = [s for s in extracted_data.get("sections", []) if len(s.get("questions", [])) > 0]

                                    with open(json_path, "w", encoding="utf-8") as f:
                                        json.dump(extracted_data, f, indent=2, ensure_ascii=False)

                                    # Format & Text analytics for new format reporting
                                    all_q = []
                                    for sec in extracted_data.get("sections", []):
                                        all_q.extend(sec.get("questions", []))

                                    tot_q = len(all_q)
                                    txt_q = 0
                                    img_q = 0
                                    for q_obj in all_q:
                                        q_text = q_obj.get("question", "") or ""
                                        raw_text = re.sub(r'<[^>]+>', '', q_text).strip()
                                        if len(raw_text) > 0 or "$" in q_text:
                                            txt_q += 1
                                        if q_obj.get("image_url"):
                                            img_q += 1

                                    is_text_fmt = (txt_q > 0)
                                    if is_text_fmt:
                                        self.text_format_count += 1
                                    else:
                                        self.image_only_count += 1

                                    test_record = {
                                        "test_name": raw_test_name,
                                        "batch_name": current_batch,
                                        "total_questions": tot_q,
                                        "text_questions": txt_q,
                                        "image_questions": img_q,
                                        "has_text": is_text_fmt,
                                        "format": "Text + LaTeX (New Format)" if is_text_fmt else "Image-Only (Legacy)",
                                        "file_name": file_name,
                                        "rel_path": f"{safe_batch_name}/{file_name}",
                                        "size_bytes": json_path.stat().st_size
                                    }
                                    self.test_details.append(test_record)

                                    # Dynamically record newly downloaded test in brain
                                    self.brain.record_downloaded(current_batch, raw_test_name)

                                    json_saved = True
                                    total_saved_count += 1
                                    fmt_label = f"✨ Text + LaTeX ({txt_q}/{tot_q} text questions)" if is_text_fmt else f"🖼️ Image-Only ({tot_q} questions)"
                                    self.log(f"      ✅ Saved JSON: {file_name} [{fmt_label}]", level="success")
                                    display_test_name = f"{raw_test_name} (Copy {dup_index})" if is_duplicate else raw_test_name
                                    self.json_files.append({
                                        "batch": current_batch,
                                        "test": display_test_name,
                                        "filename": file_name,
                                        "rel_path": f"{safe_batch_name}/{file_name}",
                                        "size_bytes": json_path.stat().st_size,
                                        "has_text": is_text_fmt,
                                        "text_questions": txt_q,
                                        "total_questions": tot_q
                                    })

                                except Exception as e:
                                    self.log(f"      ❌ Attempt {attempt}/{max_attempts} failed for {raw_test_name}: {e}", level="warning")
                                    if attempt < max_attempts and not self.stop_requested:
                                        self.log(f"      🔄 Retrying in 2 seconds... ({max_attempts - attempt} attempts left)")
                                        time.sleep(2)
                                        page.goto(batch_url)
                                        page.wait_for_load_state("networkidle")
                                        continue
                                    else:
                                        self.log(f"      ❌ All {max_attempts} attempts failed for {raw_test_name}. Skipping.", level="error")
                                        if current_batch not in self.failed_tracker:
                                            self.failed_tracker[current_batch] = []
                                        self.failed_tracker[current_batch].append(f"{raw_test_name} (Index {i+1}) - {e}")

                            page.goto(batch_url)
                            page.wait_for_load_state("networkidle")

                    except Exception as e:
                        self.log(f"❌ Failed processing batch '{current_batch}': {e}", level="error")
                        if current_batch not in self.failed_tracker:
                            self.failed_tracker[current_batch] = []
                        self.failed_tracker[current_batch].append(f"ENTIRE BATCH FAILED: {e}")

                    # Package batch files into ZIP (only JSONs, NO empty pdf folder)
                    batch_json_files = list(batch_dir.glob("*.json"))
                    if batch_json_files:
                        zip_file_name = f"{safe_batch_name}.zip"
                        zip_path = self.output_dir / zip_file_name
                        self.log(f"📦 Packaging {len(batch_json_files)} test JSONs into '{zip_file_name}'...")
                        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                            for jf in batch_json_files:
                                arcname = os.path.join(safe_batch_name, jf.name)
                                zipf.write(jf, arcname)

                        self.zip_files.append({
                            "batch": display_batch_name,
                            "filename": zip_file_name,
                            "rel_path": zip_file_name,
                            "size_bytes": zip_path.stat().st_size,
                            "test_count": len(batch_json_files)
                        })
                        self.log(f"🎉 Batch execution completed for '{display_batch_name}'! Zip size: {zip_path.stat().st_size / 1024:.1f} KB", level="success")

                browser.close()

            if self.stop_requested:
                self._finish_stopped()
                return

            self.end_time = time.time()
            elapsed_sec = int(self.end_time - (self.start_time or self.end_time))

            # Build comprehensive execution summary
            failed_batches_str = ", ".join(self.failed_tracker.keys()) if self.failed_tracker else ""
            total_failed_tests = sum(len(v) for v in self.failed_tracker.values())

            self.summary = {
                "batches_processed": len(batches_to_process),
                "total_saved": total_saved_count,
                "total_duplicates": total_duplicates_count,
                "total_skipped": total_skipped_count,
                "total_failed": total_failed_tests,
                "text_format_count": self.text_format_count,
                "image_only_count": self.image_only_count,
                "test_details": self.test_details,
                "skipped_brain_count": len(self.skipped_brain_list),
                "skipped_brain_list": self.skipped_brain_list,
                "duplicate_tracker": self.duplicate_tracker,
                "duplicate_list": self.duplicate_list,
                "skipped_tracker": self.skipped_tracker,
                "skipped_list": self.skipped_list,
                "failed_tracker": self.failed_tracker,
                "failed_batches_string": failed_batches_str,
                "elapsed_seconds": elapsed_sec,
                "zip_files": self.zip_files,
                "json_files_count": len(self.json_files)
            }

            self.state = "finished"
            self.progress(batch_total, batch_total, "Extraction completed successfully!")
            self.log("=======================================================", level="batch")
            self.log(f"🏁 EXECUTION FINISHED in {elapsed_sec}s!", level="success")
            self.log(f"✅ Saved: {total_saved_count} | ✨ With Text (New Format): {self.text_format_count} | 🖼️ Image-Only: {self.image_only_count}")
            if self.skipped_brain_list:
                self.log(f"🧠 Brain Filter: {len(self.skipped_brain_list)} already downloaded tests were skipped.")
            self.log(f"📑 Duplicates Saved: {total_duplicates_count} | ⏭️ Skipped: {total_skipped_count} | ❌ Failed: {total_failed_tests}")
            if self.failed_tracker:
                self.log(f"⚠️ Failed batches retry list: {failed_batches_str}", level="warning")
            self.log("=======================================================", level="batch")

        except Exception as e:
            self.state = "error"
            self.message = f"Error: {e}"
            self.log(f"💥 Extraction failed with exception: {e}", level="error")

    def _finish_stopped(self):
        self.state = "stopped"
        self.message = "Job aborted by user."
        self.log("🛑 Job execution was stopped.", level="warning")
