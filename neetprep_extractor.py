"""NEETprep Question Extractor for Python Runner & Quizzy.

Features:
- DuckDuckGo-powered chapter discovery (search format: "{prefix} {clean_chapter}").
- Built-in syllabus & chapter topic mapping for instant zero-latency extractions.
- Direct NEETprep GraphQL API pagination with correct schema & headers.
- Tracks exact question count per JSON file.
- Generates comprehensive failure & success summary reports (README_SUMMARY.md & extraction_summary.json).
- Thread-safe NeetprepJob class for web UI + CLI standalone runner.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import html
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# =============================================================================
# Syllabus Mapping (Physics, Chemistry, Botany, Zoology)
# =============================================================================

SYLLABUS: Dict[str, List[str]] = {
    "Physics": [
        "Units and Measurement", "Mathematical Tools", "Motion in A Straight Line", "Motion in A Plane", "Laws of Motion",
        "Work, Energy and Power", "Systems of Particles and Rotational Motion", "Gravitation", "Mechanical Properties of Solids",
        "Mechanical Properties of Fluids", "Thermal Properties of Matter", "Thermodynamics", "Kinetic Theory of Gases",
        "Oscillations", "Waves", "Electric Charges and Fields", "Electrostatic Potential and Capacitance", "Current Electricity",
        "Moving Charges and Magnetism", "Magnetism and Matter", "Electromagnetic Induction", "Alternating Current",
        "Electromagnetic Waves", "Ray Optics and Optical Instruments", "Wave Optics", "Dual Nature of Radiation and Matter",
        "Atoms", "Nuclei", "Semiconductor Electronics"
    ],
    "Chemistry": [
        "Some Basic Concepts Of Chemistry", "Structure of Atom", "Classification of Elements and Periodicity in Properties",
        "Chemical Bonding and Molecular Structure", "Thermodynamics", "Equilibrium", "Redox Reactions", "The p-Block Elements (XI)",
        "Organic Chemistry - Some Basic Principles And Techniques", "Hydrocarbons", "Solutions", "Electrochemistry", "Chemical Kinetics",
        "The p-Block Elements (XII)", "The d and f Block Elements", "Coordination Compounds", "Haloalkanes and Haloarenes",
        "Alcohols, Phenols and Ethers", "Aldehydes, Ketones and Carboxylic Acids", "Amines", "Biomolecules",
        "Reaction Mechanism - Organic Chemistry", "Differentiations & Conversions - Organic Chemistry", "Salt Analysis"
    ],
    "Botany": [
        "The Living World", "Biological Classification", "Plant Kingdom", "Morphology of Flowering Plants", "Anatomy of Flowering Plants",
        "Cell - The Unit of Life", "Cell Cycle and Cell Division", "Photosynthesis in Higher Plants", "Respiration in Plants",
        "Plant Growth and Development", "Sexual Reproduction in Flowering Plants", "Principles of Inheritance & Variation",
        "Molecular Basis of Inheritance", "Microbes in Human Welfare", "Organisms and Populations", "Ecosystem", "Biodiversity and Conservation"
    ],
    "Zoology": [
        "Animal Kingdom", "Structural Organisation in Animals", "Biomolecules", "Breathing and Exchange of Gases", "Body Fluids and Circulation",
        "Excretory Products and their Elimination", "Locomotion and Movement", "Neural Control and Coordination", "Chemical Coordination and Integration",
        "Human Reproduction", "Reproductive Health", "Evolution", "Human Health and Disease", "Biotechnology: Principles and Processes", "Biotechnology and its Applications"
    ]
}

# Chapter Base Mapping (Subject ID and Topic ID only - NO hardcoded tests/courses)
CHAPTER_BASE_MAP: Dict[str, Dict[str, Dict[str, str]]] = {
    "Physics": {
        "Units and Measurement": {"subject": "55", "chapter": "675"},
        "Mathematical Tools": {"subject": "55", "chapter": "676"},
        "Motion in A Straight Line": {"subject": "55", "chapter": "677"},
        "Motion in A Plane": {"subject": "55", "chapter": "678"},
        "Laws of Motion": {"subject": "55", "chapter": "679"},
        "Work, Energy and Power": {"subject": "55", "chapter": "680"},
        "Systems of Particles and Rotational Motion": {"subject": "55", "chapter": "681"},
        "Gravitation": {"subject": "55", "chapter": "682"},
        "Mechanical Properties of Solids": {"subject": "55", "chapter": "684"},
        "Mechanical Properties of Fluids": {"subject": "55", "chapter": "685"},
        "Thermal Properties of Matter": {"subject": "55", "chapter": "686"},
        "Thermodynamics": {"subject": "55", "chapter": "687"},
        "Kinetic Theory of Gases": {"subject": "55", "chapter": "688"},
        "Oscillations": {"subject": "55", "chapter": "689"},
        "Waves": {"subject": "55", "chapter": "690"},
        "Electric Charges and Fields": {"subject": "55", "chapter": "691"},
        "Electrostatic Potential and Capacitance": {"subject": "55", "chapter": "692"},
        "Current Electricity": {"subject": "55", "chapter": "693"},
        "Moving Charges and Magnetism": {"subject": "55", "chapter": "694"},
        "Magnetism and Matter": {"subject": "55", "chapter": "695"},
        "Electromagnetic Induction": {"subject": "55", "chapter": "696"},
        "Alternating Current": {"subject": "55", "chapter": "697"},
        "Electromagnetic Waves": {"subject": "55", "chapter": "698"},
        "Ray Optics and Optical Instruments": {"subject": "55", "chapter": "699"},
        "Wave Optics": {"subject": "55", "chapter": "700"},
        "Dual Nature of Radiation and Matter": {"subject": "55", "chapter": "701"},
        "Atoms": {"subject": "55", "chapter": "702"},
        "Nuclei": {"subject": "55", "chapter": "703"},
        "Semiconductor Electronics": {"subject": "55", "chapter": "704"},
    },
    "Chemistry": {
        "Some Basic Concepts Of Chemistry": {"subject": "4416", "chapter": "7947"},
        "Structure of Atom": {"subject": "4416", "chapter": "7923"},
        "Classification of Elements and Periodicity in Properties": {"subject": "4416", "chapter": "7927"},
        "Chemical Bonding and Molecular Structure": {"subject": "4416", "chapter": "7922"},
        "Thermodynamics": {"subject": "4416", "chapter": "7938"},
        "Equilibrium": {"subject": "4416", "chapter": "7944"},
        "Redox Reactions": {"subject": "4416", "chapter": "7945"},
        "The p-Block Elements (XI)": {"subject": "4416", "chapter": "7929"},
        "Organic Chemistry - Some Basic Principles And Techniques": {"subject": "4416", "chapter": "7933"},
        "Hydrocarbons": {"subject": "4416", "chapter": "7935"},
        "Solutions": {"subject": "4416", "chapter": "7946"},
        "Electrochemistry": {"subject": "4416", "chapter": "7949"},
        "Chemical Kinetics": {"subject": "4416", "chapter": "7925"},
        "The p-Block Elements (XII)": {"subject": "4416", "chapter": "7936"},
        "The d and f Block Elements": {"subject": "4416", "chapter": "7940"},
        "Coordination Compounds": {"subject": "4416", "chapter": "7943"},
        "Haloalkanes and Haloarenes": {"subject": "4416", "chapter": "7951"},
        "Alcohols, Phenols and Ethers": {"subject": "4416", "chapter": "7950"},
        "Aldehydes, Ketones and Carboxylic Acids": {"subject": "4416", "chapter": "7926"},
        "Amines": {"subject": "4416", "chapter": "7930"},
        "Biomolecules": {"subject": "4416", "chapter": "7932"},
        "Reaction Mechanism - Organic Chemistry": {"subject": "4416", "chapter": "7921"},
        "Differentiations & Conversions - Organic Chemistry": {"subject": "4416", "chapter": "7952"},
        "Salt Analysis": {"subject": "4416", "chapter": "11641"},
    },
    "Botany": {
        "The Living World": {"subject": "51", "chapter": "622"},
        "Biological Classification": {"subject": "51", "chapter": "623"},
        "Plant Kingdom": {"subject": "51", "chapter": "624"},
        "Morphology of Flowering Plants": {"subject": "51", "chapter": "625"},
        "Anatomy of Flowering Plants": {"subject": "51", "chapter": "626"},
        "Cell - The Unit of Life": {"subject": "51", "chapter": "627"},
        "Cell Cycle and Cell Division": {"subject": "51", "chapter": "629"},
        "Photosynthesis in Higher Plants": {"subject": "51", "chapter": "632"},
        "Respiration in Plants": {"subject": "51", "chapter": "633"},
        "Plant Growth and Development": {"subject": "51", "chapter": "634"},
        "Sexual Reproduction in Flowering Plants": {"subject": "51", "chapter": "635"},
        "Principles of Inheritance & Variation": {"subject": "51", "chapter": "636"},
        "Molecular Basis of Inheritance": {"subject": "51", "chapter": "637"},
        "Microbes in Human Welfare": {"subject": "51", "chapter": "720"},
        "Organisms and Populations": {"subject": "51", "chapter": "641"},
        "Ecosystem": {"subject": "51", "chapter": "642"},
        "Biodiversity and Conservation": {"subject": "51", "chapter": "643"},
    },
    "Zoology": {
        "Animal Kingdom": {"subject": "52", "chapter": "706"},
        "Structural Organisation in Animals": {"subject": "52", "chapter": "707"},
        "Biomolecules": {"subject": "52", "chapter": "628"},
        "Breathing and Exchange of Gases": {"subject": "52", "chapter": "709"},
        "Body Fluids and Circulation": {"subject": "52", "chapter": "710"},
        "Excretory Products and their Elimination": {"subject": "52", "chapter": "711"},
        "Locomotion and Movement": {"subject": "52", "chapter": "712"},
        "Neural Control and Coordination": {"subject": "52", "chapter": "713"},
        "Chemical Coordination and Integration": {"subject": "52", "chapter": "714"},
        "Human Reproduction": {"subject": "52", "chapter": "715"},
        "Reproductive Health": {"subject": "52", "chapter": "716"},
        "Evolution": {"subject": "52", "chapter": "717"},
        "Human Health and Disease": {"subject": "52", "chapter": "718"},
        "Biotechnology: Principles and Processes": {"subject": "52", "chapter": "719"},
        "Biotechnology and its Applications": {"subject": "52", "chapter": "721"},
    }
}


def fetch_chapter_bank_links_http(base_url: str) -> List[Tuple[str, str]]:
    """Fetches HTML of a chapter page directly via HTTP and extracts all available question bank links."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    req = urllib.request.Request(base_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            page_html = resp.read().decode('utf-8', errors='replace')
    except Exception:
        return []
    
    matches = re.findall(r'<a[^>]+href="([^"]+testId=[^"]+)"[^>]*>(.*?)</a>', page_html, re.DOTALL)
    results: List[Tuple[str, str]] = []
    for href, text in matches:
        clean_text = re.sub(r'<[^>]+>', '', text).strip()
        clean_text = html.unescape(clean_text)
        clean_href = html.unescape(href)
        if clean_href.startswith('/'):
            clean_href = "https://www.neetprep.com" + clean_href
        if clean_text and clean_href:
            results.append((clean_text, clean_href))
    return results


def find_best_test_link(available_links: List[Tuple[str, str]], user_prefix: str) -> Tuple[Optional[str], Optional[str]]:
    """Matches user prefix (e.g. 'Padma Shri H C Verma...', 'jee mains...', 'NCERT Exemplar...') against the question banks found on a chapter page."""
    clean_prefix = re.sub(r'[\(\)\[\]\/\-\:]', ' ', user_prefix.lower())
    noise = {'neetprep', 'mcqs', 'mcq', 'based', 'questions', 'question', 'bank', 'site', 'https', 'com'}
    prefix_tokens = [w for w in clean_prefix.split() if w not in noise]

    best_link: Optional[str] = None
    best_score = -1
    best_text: Optional[str] = None

    for link_text, link_url in available_links:
        clean_text = re.sub(r'[\(\)\[\]\/\-\:]', ' ', link_text.lower())
        text_tokens = [w for w in clean_text.split() if w not in noise]

        score = 0
        for pt in prefix_tokens:
            if pt in text_tokens:
                score += 2
            elif any(pt in tt for tt in text_tokens):
                score += 1

        # Specific high-confidence boosts (supporting singular and plural forms)
        if any(w in prefix_tokens for w in ['jee']) and any(w in text_tokens for w in ['jee']):
            score += 10
        if any(w in prefix_tokens for w in ['main', 'mains']) and any(w in text_tokens for w in ['main', 'mains']):
            score += 6
        if any(w in prefix_tokens for w in ['verma', 'hcv']) and any(w in text_tokens for w in ['verma', 'hcv', 'h']):
            score += 10
        if any(w in prefix_tokens for w in ['exemplar']) and any(w in text_tokens for w in ['exemplar']):
            score += 10
        if any(w in prefix_tokens for w in ['exercise', 'exercises']) and any(w in text_tokens for w in ['exercise', 'exercises']):
            score += 8
        if any(w in prefix_tokens for w in ['solved', 'example', 'examples']) and any(w in text_tokens for w in ['solved', 'example', 'examples']):
            score += 8
        if any(w in prefix_tokens for w in ['past', 'pyq', 'pyqs']) and any(w in text_tokens for w in ['past', 'pyq', 'pyqs']):
            score += 5
        if any(w in prefix_tokens for w in ['absorb']) and any(w in text_tokens for w in ['absorb']):
            score += 10

        if score > best_score:
            best_score = score
            best_link = link_url
            best_text = link_text

    min_required = 4
    if any(k in prefix_tokens for k in ['verma', 'hcv', 'jee', 'exemplar', 'main', 'mains']):
        min_required = 8

    if best_score >= min_required and best_link:
        return best_link, best_text
    return None, None




# =============================================================================
# Helper Utilities
# =============================================================================

def sanitize_filename(name: str) -> str:
    """Sanitizes names for safe file paths."""
    return re.sub(r'[\/\\?%*:|"<>#]', '_', (name or "untitled")).strip()


def safe_slug(text: str) -> str:
    """Generates clean slug for prefixes and files."""
    return re.sub(r'[^a-zA-Z0-9_\-]', '_', text or "").strip('_')




# =============================================================================
# DuckDuckGo Search Link Discovery
# =============================================================================

def search_duckduckgo_http(query: str, timeout: int = 15) -> Optional[str]:
    """Queries DuckDuckGo HTML endpoint via POST and extracts target NEETprep URL."""
    url = "https://html.duckduckgo.com/html/"
    data = urllib.parse.urlencode({"q": query, "b": ""}).encode("utf-8")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Origin": "https://html.duckduckgo.com",
        "Referer": "https://html.duckduckgo.com/"
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # 1. Search in result__a links
        links = re.findall(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"', html)
        for l in links:
            clean = unescape_and_clean_url(l)
            if clean and "neetprep.com/questions" in clean:
                return clean

        # 2. Search anywhere in HTML for neetprep question links
        matches = re.findall(r'href="([^"]*neetprep\.com\/questions[^"]*)"', html, re.IGNORECASE)
        for m in matches:
            clean = unescape_and_clean_url(m)
            if clean and "neetprep.com/questions/" in clean:
                return clean

        # 3. Search for DDG redirect links: uddg=...
        uddg_matches = re.findall(r'uddg=([^\s&"]+)', html, re.IGNORECASE)
        for m in uddg_matches:
            clean = unescape_and_clean_url(urllib.parse.unquote(m))
            if clean and "neetprep.com/questions/" in clean:
                return clean

        return None
    except Exception:
        return None


def unescape_and_clean_url(raw: str) -> Optional[str]:
    """Cleans DDG redirect URLs and escapes HTML entities."""
    clean = raw.replace("&amp;", "&")
    if clean.startswith("//"):
        clean = "https:" + clean

    # Decode DDG uddg parameter
    if "uddg=" in clean:
        parsed = urllib.parse.urlparse(clean)
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            clean = qs["uddg"][0]

    unquoted = urllib.parse.unquote(clean)
    return unquoted if unquoted.startswith("http") else None


def resolve_chapter_url(
    subject: str,
    chapter: str,
    prefix: str,
    log_fn: Optional[Callable[[str], None]] = None,
    browser_session: Optional[Any] = None
) -> Tuple[Optional[str], str]:
    """
    Dynamically resolves the target question bank URL for (subject, chapter, prefix).
    1. If in CHAPTER_BASE_MAP: builds the chapter base URL, inspects all question banks on the page
       via HTTP (or in-browser), and matches the bank corresponding to user's prefix.
    2. If unlisted: searches DuckDuckGo for '{prefix} {clean_chapter}'.
    """
    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    # 1. Check CHAPTER_BASE_MAP for known subject & chapter
    if subject in CHAPTER_BASE_MAP and chapter in CHAPTER_BASE_MAP[subject]:
        mapping = CHAPTER_BASE_MAP[subject][chapter]
        safe_ch = re.sub(r'[^a-zA-Z0-9]', '-', chapter).strip('-')
        base_url = f"https://www.neetprep.com/questions/{mapping['subject']}-{subject}/{mapping['chapter']}-{safe_ch}"

        _log(f"🔎 [Search Request] Prefix: \"{prefix}\" | Chapter: \"{chapter}\"")
        _log(f"🌐 [Inspecting Page] {base_url}")

        # 1a. Fast direct HTTP inspect of NEETprep question banks
        http_links = fetch_chapter_bank_links_http(base_url)
        if http_links:
            _log(f"📋 Found {len(http_links)} question banks on chapter page. Matching against \"{prefix}\"...")
            matched_url, matched_title = find_best_test_link(http_links, prefix)
            if matched_url:
                _log(f"🎯 [Question Bank Matched] \"{matched_title}\"")
                _log(f"🔗 Target URL: {matched_url}")
                return matched_url, f"bank_http: {matched_title}"

        # 1b. In-browser inspect via Chromium session if HTTP didn't match or failed
        if browser_session:
            _log(f"🌐 Opening chapter page in Chromium browser to inspect question banks...")
            matched_url, matched_title = browser_session.resolve_bank_link(base_url, prefix)
            if matched_url:
                _log(f"🎯 [In-Browser Matched Bank] \"{matched_title}\"")
                _log(f"🔗 Target URL: {matched_url}")
                return matched_url, f"bank_browser: {matched_title}"

        _log(f"⚠️ [No Match] Chapter '{chapter}' has no question bank matching prefix: \"{prefix}\"")
        if http_links:
            _log(f"📋 Available question banks found on this chapter page:")
            for title, _ in http_links[:10]:
                _log(f"   • {title}")
        return None, "no_matching_bank"

    # 2. Dynamic search as requested by user for custom or unlisted chapters:
    clean_ch = re.sub(r'[,.\-_]', ' ', chapter).strip()
    clean_ch = re.sub(r'\s+', ' ', clean_ch).lower()
    
    query = f"{prefix} {clean_ch}"
    _log(f"🔍 [DuckDuckGo Search Query] \"{query}\"")

    target_url = search_duckduckgo_http(query)
    if not target_url:
        fallback_q = f"site:neetprep.com/questions {prefix} {clean_ch}"
        _log(f"🔍 [Fallback DDG Search] \"{fallback_q}\"")
        target_url = search_duckduckgo_http(fallback_q)

    if target_url:
        _log(f"🔗 [DDG Link Found] {target_url}")
        return target_url, "duckduckgo_http"

    _log(f"❌ [Not Found] Search engines returned 0 results for: \"{query}\"")
    return None, "not_found"




# =============================================================================
# URL Parameter Extraction & GraphQL Engine
# =============================================================================

def parse_neetprep_url(url: str) -> Tuple[Optional[str], Optional[str], str, Optional[str]]:
    """Extracts subjectNum, chapterNum, courseNum, testId from URL."""
    parsed = urllib.parse.urlparse(url)
    path_parts = [p for p in parsed.path.split('/') if p]

    # Expected: ['questions', '2310-Physics', '7972-Units-Measurement']
    if len(path_parts) < 3 or path_parts[0] != 'questions':
        return None, None, "287", None

    subject_num = path_parts[1].split('-')[0]
    chapter_num = path_parts[2].split('-')[0]

    qs = urllib.parse.parse_qs(parsed.query)
    course_num = qs.get('courseId', ['287'])[0]
    raw_test_id = qs.get('testId', [None])[0]

    clean_test_id = raw_test_id.split('-')[0] if raw_test_id else None
    return subject_num, chapter_num, course_num, clean_test_id


def build_graphql_query(course_num: str, subject_num: str, chapter_num: str, test_id: Optional[str]) -> str:
    """Builds NEETprep GraphQL query matching frontend application structure (exact closing braces)."""
    course_id_b64 = base64.b64encode(f"Course:{course_num}".encode("utf-8")).decode("utf-8")
    subject_id_b64 = base64.b64encode(f"Subject:{subject_num}".encode("utf-8")).decode("utf-8")
    chapter_id_b64 = base64.b64encode(f"Topic:{chapter_num}".encode("utf-8")).decode("utf-8")

    test_id_param = f'testId:"{test_id}",' if test_id else ""
    is_strict = "true" if test_id else "false"

    query = (
        'query QuestionsRoute {root {...F4}} '
        'fragment F0 on Question {id,studentNote {id,note,studentAttachImgUri}} '
        'fragment F1 on Question {id} '
        'fragment F2 on Question {id,...F1} '
        'fragment F3 on Question {'
        'premium,id,question,questionNum,questionTitle,explanationExists,options @include(if:true),'
        '_ncertSentences2ASk0H:ncertSentences(first:1) {total},_videoSentences47vAf0:videoSentences(first:1) {total},'
        'correctOptionIndex,ncert,explanation,paidAccess,explanationWithAudio,explanationWithoutAudio,'
        'bookmarkQuestion @include(if:true) {id,questionId,userId,priority},'
        '_subTopics1oRyyE:subTopics(first:10) @include(if:true) {edges {node {id,name},id,cursor},pageInfo {hasNextPage,hasPreviousPage}},'
        'userAnswer @include(if:true) {id,userAnswer,durationInSec,incorrectAnswerReason,incorrectAnswerOther},'
        '_questionDoubts24kVgG:questionDoubts(first:1,hasAnswer:true) {edges {node {id},cursor},pageInfo {hasNextPage,hasPreviousPage}},'
        '_hints1FhFeX:hints(first:10) {edges {node {id,hint,position,hintVideoLink {id,name,url,time,video {id,name,_topics3kIoNJ:topics(first:1) {edges {node {id,name,subject {id,name}},id,cursor},pageInfo {hasNextPage,hasPreviousPage}}}}},cursor},pageInfo {hasNextPage,hasPreviousPage}},'
        'analytics {correctPercentage,id},analytics @include(if:true) {correctPercentage,option1Percentage,option2Percentage,option3Percentage,option4Percentage,id},'
        '_questionDetails3wxeRf:questionDetails(last:1) @include(if:true) {edges {node {year,exam,id},cursor},pageInfo {hasNextPage,hasPreviousPage}},...F0,...F1,...F2} '
        f'fragment F4 on Root {{ fetchedData:allQuestions(courseId:"{course_id_b64}",subjectId:"{subject_id_b64}",chapterId:"{chapter_id_b64}",first:50,offset:$OFFSET,strict:{is_strict},pastYear:false,questionType:[],masterclassType:[],questionSource:[],questionLevel:[],{test_id_param}bookmark:false,incorrect:false,ncert:false) '
        '{totalCount,edges {node {id,questionNum,questionTitle,options,explanationText,correctOptionIndex,analytics {correctPercentage,id},'
        '_questionDetails3wxeRf:questionDetails(last:1) {edges {node {id,year,exam},cursor},pageInfo {hasNextPage,hasPreviousPage}},'
        '_topics3kIoNJ:topics(first:1) {edges {node {id,name},id,cursor},pageInfo {hasNextPage,hasPreviousPage}},...F3},cursor},pageInfo {hasNextPage,hasPreviousPage}}}'
    )
    return query


# =============================================================================
# In-Browser Playwright Session (CloudFront WAF & Datacenter IP Bypass)
# =============================================================================

class PlaywrightNeetprepSession:
    """Persistent headless Chromium session to bypass CloudFront WAF on datacenter/cloud IPs."""
    def __init__(self, log_fn: Optional[Callable[[str], None]] = None):
        self.log_fn = log_fn or print
        self._pw = None
        self._browser = None
        self._page = None
        self.is_active = False

    def start(self) -> bool:
        if self.is_active and self._page:
            return True
        try:
            from playwright.sync_api import sync_playwright
            self.log_fn("🌐 Launching Chromium browser session for CloudFront bypass...")
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            ctx = self._browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            self._page = ctx.new_page()
            try:
                from playwright_stealth import Stealth
                Stealth().use_sync(self._page)
            except Exception:
                pass

            self.log_fn("⏳ Initializing NEETprep browser session...")
            self._page.goto("https://www.neetprep.com/", wait_until="domcontentloaded", timeout=25000)
            self.is_active = True
            self.log_fn("✅ In-browser engine ready. CloudFront bypassed.")
            return True
        except Exception as e:
            self.log_fn(f"⚠️ Playwright initialization error: {e}")
            self.close()
            return False

    def resolve_bank_link(self, base_url: str, user_prefix: str) -> Tuple[Optional[str], Optional[str]]:
        """Opens the chapter base URL in Chromium and matches the question bank corresponding to user_prefix."""
        if not self.is_active:
            if not self.start():
                return None, None
        try:
            self._page.goto(base_url, wait_until="domcontentloaded", timeout=25000)
            self._page.wait_for_timeout(1500)
            links = self._page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('a'))
                        .map(a => [a.innerText.trim(), a.href])
                        .filter(item => item[0].length > 0 && item[1].includes('questions/') && item[1].includes('testId='));
                }
            """)
            return find_best_test_link(links, user_prefix)
        except Exception as ex:
            self.log_fn(f"⚠️ In-browser bank resolution warning: {ex}")
            return None, None

    def fetch_graphql(self, query: str, offset: int, referer_url: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not self.is_active:
            if not self.start():
                return None, "Failed to start in-browser engine (Playwright not available)"
        try:
            if referer_url and self._page and self._page.url != referer_url:
                try:
                    self._page.goto(referer_url, wait_until="domcontentloaded", timeout=25000)
                    self._page.wait_for_timeout(1000)
                except Exception as e:
                    self.log_fn(f"⚠️ Chapter context navigation warning: {e}")

            res = self._page.evaluate("""
                async ({ query, offset }) => {
                    try {
                        const res = await fetch("/graphql/QuestionsRoute", {
                            method: "POST",
                            headers: {
                                "Content-Type": "application/json",
                                "Accept": "*/*"
                            },
                            body: JSON.stringify({
                                query: query.replace('$OFFSET', offset),
                                variables: {}
                            })
                        });
                        const text = await res.text();
                        if (!res.ok) {
                            return { ok: false, status: res.status, error: text.slice(0, 150) };
                        }
                        try {
                            const data = JSON.parse(text);
                            return { ok: true, data: data };
                        } catch (e) {
                            return { ok: false, status: 200, error: "Invalid JSON response: " + text.slice(0, 100) };
                        }
                    } catch (netErr) {
                        return { ok: false, status: 0, error: netErr.message || String(netErr) };
                    }
                }
            """, {"query": query, "offset": offset})

            if not res.get("ok"):
                return None, f"HTTP Error {res.get('status')}: {res.get('error')}"
            return res.get("data"), None
        except Exception as ex:
            return None, f"Playwright evaluate error: {ex}"

    def close(self):
        try:
            if self._page:
                self._page.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._page = None
        self._browser = None
        self._pw = None
        self.is_active = False


def fetch_chapter_questions(
    subject_num: str,
    chapter_num: str,
    course_num: str,
    test_id: Optional[str],
    referer_url: Optional[str] = None,
    log_fn: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    browser_session: Optional[PlaywrightNeetprepSession] = None
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Fetches all questions for a chapter via NEETprep GraphQL with offset pagination and auto-CloudFront bypass."""
    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    base_query = build_graphql_query(course_num, subject_num, chapter_num, test_id)
    endpoint = "https://www.neetprep.com/graphql/QuestionsRoute"
    ref = referer_url or f"https://www.neetprep.com/questions/{subject_num}-Subject/{chapter_num}-Topic"
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Origin": "https://www.neetprep.com",
        "Referer": ref
    }

    offset = 0
    total = 1
    all_questions: List[Dict[str, Any]] = []

    while offset < total:
        if cancel_check and cancel_check():
            return all_questions, "Extraction cancelled by user."

        current_query = base_query.replace('$OFFSET', str(offset))
        data = None

        # 1. If in-browser session is already active, use it directly (0 wasted network attempts)
        if browser_session and browser_session.is_active:
            data, err = browser_session.fetch_graphql(base_query, offset, referer_url=ref)
            if err:
                return all_questions, err
        else:
            # 2. Try fast direct HTTP first
            payload = json.dumps({"query": current_query, "variables": {}}).encode("utf-8")
            req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")

            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as ex:
                err_body = ex.read().decode("utf-8", errors="replace")[:120]
                # If CloudFront WAF 403 / 503 is detected on datacenter IP:
                if ex.code in (403, 503) or "DOCTYPE" in err_body or "CloudFront" in err_body:
                    if browser_session:
                        _log("   🛡️ CloudFront WAF 403 detected on datacenter IP. Switching to Playwright in-browser engine...")
                        data, err = browser_session.fetch_graphql(base_query, offset, referer_url=ref)
                        if err:
                            return all_questions, err
                    else:
                        return all_questions, f"HTTP Error {ex.code}: {err_body}"
                else:
                    return all_questions, f"HTTP Error {ex.code}: {err_body}"
            except Exception as ex:
                if browser_session:
                    _log(f"   ⚠️ Direct HTTP error ({ex}). Retrying via Playwright in-browser engine...")
                    data, err = browser_session.fetch_graphql(base_query, offset, referer_url=ref)
                    if err:
                        return all_questions, err
                else:
                    return all_questions, f"Request failed: {ex}"


        if not data:
            return all_questions, "Empty response received from GraphQL query"

        root_data = data.get("data", {}).get("root", {}).get("fetchedData")
        if not root_data:
            err_msg = data.get("errors", [{}])[0].get("message", "No fetchedData in GraphQL response")
            return all_questions, f"GraphQL Error: {err_msg}"

        total = root_data.get("totalCount", 0)
        edges = root_data.get("edges", [])
        for e in edges:
            if "node" in e and isinstance(e["node"], dict):
                all_questions.append(e["node"])

        _log(f"   📥 Progress: {len(all_questions)} / {total} questions fetched (offset {offset})")
        offset += 50
        time.sleep(0.35)

    return all_questions, None


# =============================================================================
# Summary Reporting System
# =============================================================================

class ExtractionSummary:
    """Detailed summary tracking question counts per JSON and chapter failure reasons."""
    def __init__(self, prefix: str, target_subjects: List[str]):
        self.prefix = prefix
        self.target_subjects = target_subjects
        self.start_time = datetime.datetime.now()
        self.end_time: Optional[datetime.datetime] = None
        self.chapters_succeeded: List[Dict[str, Any]] = []
        self.chapters_failed: List[Dict[str, Any]] = []
        self.total_questions = 0

    def record_success(self, subject: str, chapter: str, count: int, filename: str, url: str, time_taken: float):
        self.chapters_succeeded.append({
            "subject": subject,
            "chapter": chapter,
            "question_count": count,
            "filename": filename,
            "url": url,
            "time_taken_seconds": round(time_taken, 2),
            "status": "SUCCESS"
        })
        self.total_questions += count

    def record_failure(self, subject: str, chapter: str, reason: str, url: Optional[str] = None):
        self.chapters_failed.append({
            "subject": subject,
            "chapter": chapter,
            "reason": reason,
            "url": url or "N/A",
            "status": "FAILED"
        })

    def finish(self):
        self.end_time = datetime.datetime.now()

    @property
    def total_attempted(self) -> int:
        return len(self.chapters_succeeded) + len(self.chapters_failed)

    @property
    def duration_seconds(self) -> float:
        end = self.end_time or datetime.datetime.now()
        return round((end - self.start_time).total_seconds(), 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prefix": self.prefix,
            "target_subjects": self.target_subjects,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": self.duration_seconds,
            "total_chapters_attempted": self.total_attempted,
            "total_chapters_succeeded": len(self.chapters_succeeded),
            "total_chapters_failed": len(self.chapters_failed),
            "total_questions_extracted": self.total_questions,
            "success_rate_percent": round((len(self.chapters_succeeded) / max(1, self.total_attempted)) * 100, 1),
            "succeeded_chapters": self.chapters_succeeded,
            "failed_chapters": self.chapters_failed
        }

    def generate_markdown(self) -> str:
        """Generates clean markdown report inside ZIP archive."""
        rate = round((len(self.chapters_succeeded) / max(1, self.total_attempted)) * 100, 1)
        lines = [
            f"# 📊 NEETprep Extraction Summary Report",
            f"",
            f"- **Execution Date**: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **Filter Prefix**: `{self.prefix}`",
            f"- **Duration**: {self.duration_seconds} seconds",
            f"- **Total Chapters Attempted**: {self.total_attempted}",
            f"- **Successfully Extracted**: ✅ {len(self.chapters_succeeded)} chapters",
            f"- **Failed / Missing**: ❌ {len(self.chapters_failed)} chapters",
            f"- **Total Questions Extracted**: 🎯 **{self.total_questions} questions**",
            f"- **Success Rate**: **{rate}%**",
            f"",
            f"---",
            f"",
            f"## 📁 Questions Extracted Per Chapter / File",
            f"",
            f"| Subject | Chapter Name | Questions | Output File |",
            f"| :--- | :--- | :---: | :--- |"
        ]

        if not self.chapters_succeeded:
            lines.append("| - | *No chapters successfully extracted* | 0 | - |")
        else:
            for item in self.chapters_succeeded:
                lines.append(f"| {item['subject']} | {item['chapter']} | **{item['question_count']}** | `{item['filename']}` |")

        lines.extend([
            f"",
            f"---",
            f"",
            f"## ⚠️ Chapters That Failed / Had Zero Questions",
            f""
        ])

        if not self.chapters_failed:
            lines.append("🎉 **Zero failures! All chapters were successfully discovered and downloaded.**")
        else:
            lines.extend([
                f"| Subject | Chapter Name | Failure Reason | Search URL |",
                f"| :--- | :--- | :--- | :--- |"
            ])
            for item in self.chapters_failed:
                lines.append(f"| {item['subject']} | {item['chapter']} | ❌ {item['reason']} | `{item['url']}` |")

        lines.extend([
            f"",
            f"---",
            f"*Generated automatically by NEETprep Extractor Studio for Quizzy.*"
        ])
        return "\n".join(lines)


# =============================================================================
# Background Job Runner (for app.py & Web Studio UI)
# =============================================================================

class NeetprepJob:
    """Thread-safe background job for web studio execution with live progress logs."""
    def __init__(
        self,
        job_id: str,
        output_dir: Path,
        prefix: str = "jee mains mcq [2014-2026] neetprep",
        target_subjects: Optional[List[str]] = None,
        target_chapters: Optional[List[str]] = None
    ):
        self.id = job_id
        self.output_dir = output_dir
        self.prefix = prefix
        self.target_subjects = target_subjects or ["All"]
        self.target_chapters = target_chapters or []

        self.state = "idle"
        self.message = "Job initialized"
        self.done = 0
        self.total = 0
        self.active_chapter = ""
        self.active_subject = ""
        self.logs: List[str] = []
        self._cancel_requested = False
        self._thread: Optional[threading.Thread] = None

        self.summary: Optional[ExtractionSummary] = None
        self.output_zip_path: Optional[Path] = None
        self.browser_session: Optional[PlaywrightNeetprepSession] = PlaywrightNeetprepSession(log_fn=self.log)

    def log(self, text: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        msg = f"[{ts}] {text}"
        self.logs.append(msg)
        print(msg)

    def cancel(self):
        self._cancel_requested = True
        self.message = "Cancellation requested..."
        self.log("⚠️ Cancellation signal received.")
        if self.browser_session:
            self.browser_session.close()

    def start(self):
        self.state = "running"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._execute()
        except Exception as e:
            self.state = "failed"
            self.message = f"Error: {e}"
            self.log(f"❌ Unhandled Exception: {e}")

    def _execute(self):
        try:
            self._execute_core()
        finally:
            if self.browser_session:
                self.browser_session.close()

    def _execute_core(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        job_work_dir = self.output_dir / f"neetprep_{self.id}"
        job_work_dir.mkdir(parents=True, exist_ok=True)

        self.summary = ExtractionSummary(self.prefix, self.target_subjects)

        # 1. Build Task Queue
        tasks: List[Dict[str, str]] = []
        for subject, chapters in SYLLABUS.items():
            if "All" in self.target_subjects or subject in self.target_subjects:
                for chapter in chapters:
                    if not self.target_chapters or chapter in self.target_chapters:
                        tasks.append({"subject": subject, "chapter": chapter})

        self.total = len(tasks)
        self.done = 0

        self.log(f"========================================================")
        self.log(f"🚀 Starting NEETprep Question Extractor")
        self.log(f"🎯 Search Prefix  : \"{self.prefix}\"")
        self.log(f"📚 Target Subjects: {', '.join(self.target_subjects)}")
        self.log(f"📋 Queued Chapters: {self.total}")
        self.log(f"========================================================")

        if self.total == 0:
            self.state = "completed"
            self.message = "No matching chapters found."
            return

        # 2. Iterate and Fetch
        for idx, task in enumerate(tasks, start=1):
            if self._cancel_requested:
                self.state = "cancelled"
                self.message = "Extraction stopped by user."
                self.log("🛑 Job cancelled.")
                break

            subject = task["subject"]
            chapter = task["chapter"]
            self.active_subject = subject
            self.active_chapter = chapter
            self.message = f"[{idx}/{self.total}] Processing {subject} - {chapter}"
            self.log(f"\n========================================================")
            self.log(f"🚀 [{idx}/{self.total}] Chapter: [{subject}] {chapter}")
            self.log(f"🔎 Searching For  : Prefix=\"{self.prefix}\" + Chapter=\"{chapter}\"")

            start_t = time.time()

            # Resolve link via Database / DuckDuckGo / Dynamic Question Bank Matcher (NO CACHE)
            target_url, source = resolve_chapter_url(
                subject, chapter, self.prefix, log_fn=self.log, browser_session=self.browser_session
            )

            if not target_url:
                if source == "no_matching_bank":
                    err_msg = f"No question bank matching prefix '{self.prefix}' exists for this chapter"
                else:
                    err_msg = f"No valid NEETprep question bank found for prefix '{self.prefix}'"
                self.log(f"⚠️ {err_msg}")
                self.summary.record_failure(subject, chapter, err_msg)
                self.done += 1
                continue


            # Parse IDs
            subject_num, chapter_num, course_num, test_id = parse_neetprep_url(target_url)
            if not subject_num or not chapter_num:
                err_msg = f"Failed to parse subject/chapter IDs from URL: {target_url}"
                self.log(f"❌ {err_msg}")
                self.summary.record_failure(subject, chapter, err_msg, target_url)
                self.done += 1
                continue

            # Fetch Questions via GraphQL
            self.log(f"⏳ Querying NEETprep GraphQL (Subject: {subject_num}, Topic: {chapter_num}, Course: {course_num})...")
            questions, err = fetch_chapter_questions(
                subject_num=subject_num,
                chapter_num=chapter_num,
                course_num=course_num,
                test_id=test_id,
                referer_url=target_url,
                log_fn=self.log,
                cancel_check=lambda: self._cancel_requested,
                browser_session=self.browser_session
            )

            time_taken = time.time() - start_t

            if err:
                self.log(f"❌ Error fetching questions: {err}")
                self.summary.record_failure(subject, chapter, err, target_url)
            elif not questions:
                err_msg = "Received 0 questions from GraphQL API"
                self.log(f"⚠️ {err_msg}")
                self.summary.record_failure(subject, chapter, err_msg, target_url)
            else:
                # Save JSON file
                safe_prefix = safe_slug(self.prefix)
                safe_subj = safe_slug(subject)
                safe_chap = safe_slug(chapter)
                file_name = f"{safe_prefix}_{safe_subj}_{safe_chap}.json"
                file_path = job_work_dir / file_name

                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(questions, f, indent=2, ensure_ascii=False)

                self.log(f"✅ Saved {len(questions)} questions to `{file_name}` in {time_taken:.1f}s")
                self.summary.record_success(subject, chapter, len(questions), file_name, target_url, time_taken)

            self.done += 1

        # 3. Finish and Create Summary Documents
        self.summary.finish()
        summary_dict = self.summary.to_dict()
        markdown_report = self.summary.generate_markdown()

        summary_json_path = job_work_dir / "extraction_summary.json"
        with open(summary_json_path, "w", encoding="utf-8") as f:
            json.dump(summary_dict, f, indent=2, ensure_ascii=False)

        summary_md_path = job_work_dir / "README_SUMMARY.md"
        with open(summary_md_path, "w", encoding="utf-8") as f:
            f.write(markdown_report)

        # 4. Bundle into Downloadable ZIP Archive
        safe_prefix = safe_slug(self.prefix) or "neetprep"
        zip_filename = f"{safe_prefix}_Extracted_Bank_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        zip_filepath = self.output_dir / zip_filename

        self.log(f"\n📦 Packaging all chapters and summary reports into `{zip_filename}`...")
        with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as z:
            for item in job_work_dir.iterdir():
                if item.is_file():
                    z.write(item, arcname=item.name)

        self.output_zip_path = zip_filepath

        try:
            shutil.rmtree(job_work_dir, ignore_errors=True)
        except Exception:
            pass

        # 5. Final Summary Output
        self.log("\n" + "=" * 60)
        self.log("📊 EXTRACTION SUMMARY:")
        self.log(f"   • Total Chapters Attempted: {self.summary.total_attempted}")
        self.log(f"   • Successfully Extracted  : {len(self.summary.chapters_succeeded)} chapters ({self.summary.total_questions} questions)")
        self.log(f"   • Failed Chapters         : {len(self.summary.chapters_failed)} chapters")
        self.log(f"   • Total Time Taken        : {self.summary.duration_seconds}s")
        self.log(f"   • ZIP Archive             : {zip_filepath.name}")
        if self.summary.chapters_failed:
            self.log("   ❌ FAILED CHAPTERS LIST:")
            for failed in self.summary.chapters_failed:
                self.log(f"      - [{failed['subject']}] {failed['chapter']} (Reason: {failed['reason']})")
        self.log("=" * 60)

        if not self._cancel_requested:
            self.state = "completed"
            self.message = f"Finished! Extracted {self.summary.total_questions} questions across {len(self.summary.chapters_succeeded)} chapters."


# =============================================================================
# CLI Standalone Entrypoint
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="NEETprep DuckDuckGo + GraphQL Extractor")
    parser.add_argument("--prefix", type=str, default="jee mains mcq [2014-2026] neetprep", help="Search prefix / filter tag")
    parser.add_argument("--subject", type=str, default="All", choices=["All", "Physics", "Chemistry", "Botany", "Zoology"], help="Target subject")
    parser.add_argument("--chapter", type=str, default="", help="Optional single chapter name to extract")
    parser.add_argument("--output-dir", type=str, default="./NEETPrep_Downloads", help="Directory to save downloads")
    args = parser.parse_args()

    out_dir = Path(args.output_dir).resolve()
    target_subjects = ["All"] if args.subject == "All" else [args.subject]
    target_chapters = [args.chapter] if args.chapter else []

    job_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    job = NeetprepJob(
        job_id=job_id,
        output_dir=out_dir,
        prefix=args.prefix,
        target_subjects=target_subjects,
        target_chapters=target_chapters
    )

    print("🚀 Launching NEETprep Extractor in CLI mode...")
    job._execute()


if __name__ == "__main__":
    main()
