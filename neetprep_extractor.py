"""NEETprep Question Extractor for Python Runner & Quizzy.

Features:
- Bulk Extraction Mode: Extracts all available question banks per chapter into structured subfolders:
    <Subject>/<Chapter>/<Bank_Title>.json
- Exclude Filter: Skips banks matching custom exclude keywords (e.g. "0.5, absorb, verma, jee").
- Single Prefix Search Mode: Discovers specific banks via prefix matching & DuckDuckGo.
- High-precision disambiguation for Recommended MCQs vs Recommended PYQs, HC Verma, JEE Mains, Exemplar, etc.
- Direct NEETprep GraphQL API pagination with schema & auto-CloudFront bypass.
- Tracks exact question count per JSON file and packages structured ZIP archive.
- Generates comprehensive markdown report (README_SUMMARY.md) & JSON summary (extraction_summary.json).
- Thread-safe NeetprepJob class for Web Studio (app.py) + Standalone CLI runner.
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
    """Matches user prefix (e.g. 'Padma Shri H C Verma...', 'jee mains...', 'Recommended mcqs...', 'NCERT Exemplar...') against the question banks found on a chapter page."""
    raw_prefix_lower = user_prefix.lower()
    clean_prefix = re.sub(r'[\(\)\[\]\/\-\:]', ' ', raw_prefix_lower)
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
        if any(w in prefix_tokens for w in ['recommended']) and any(w in text_tokens for w in ['recommended']):
            score += 10
            # Disambiguate Recommended MCQs vs Recommended PYQs
            if any(w in raw_prefix_lower for w in ['mcq', 'mcqs']) and any(w in clean_text for w in ['mcq', 'mcqs']):
                score += 6
            if any(w in raw_prefix_lower for w in ['pyq', 'pyqs']) and any(w in clean_text for w in ['pyq', 'pyqs']):
                score += 6

        if score > best_score:
            best_score = score
            best_link = link_url
            best_text = link_text

    min_required = 4
    if any(k in prefix_tokens for k in ['verma', 'hcv', 'jee', 'exemplar', 'main', 'mains', 'recommended']):
        min_required = 8

    if best_score >= min_required and best_link:
        return best_link, best_text
    return None, None


def get_chapter_all_banks_filtered(
    base_url: str,
    exclude_keywords: str,
    browser_session: Optional[Any] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Extracts all distinct question banks from a chapter page and filters out
    any matching the comma-separated exclude_keywords.
    """
    raw_links = fetch_chapter_bank_links_http(base_url)
    if not raw_links and browser_session:
        raw_links = browser_session.get_all_bank_links(base_url)

    exclude_tokens = [tok.strip().lower() for tok in exclude_keywords.split(",") if tok.strip()]

    queued: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    seen_test_ids = set()

    for title, url in raw_links:
        subj_num, chap_num, course_num, test_id = parse_neetprep_url(url)
        if not test_id or test_id in seen_test_ids:
            continue
        seen_test_ids.add(test_id)

        title_lower = title.lower()
        matched_exclude = next((ex for ex in exclude_tokens if ex in title_lower), None)

        bank_info = {
            "title": title,
            "url": url,
            "subject_num": subj_num,
            "chapter_num": chap_num,
            "course_num": course_num,
            "test_id": test_id
        }

        if matched_exclude:
            bank_info["exclude_rule"] = matched_exclude
            skipped.append(bank_info)
        else:
            queued.append(bank_info)

    return queued, skipped


# =============================================================================
# Helper Utilities
# =============================================================================

def sanitize_filename(name: str) -> str:
    """Sanitizes names for safe file paths."""
    clean = re.sub(r'[\/\\?%*:|"<>#]', '_', (name or "untitled")).strip()
    clean = re.sub(r'\s+', '_', clean)
    clean = re.sub(r'_+', '_', clean)
    return clean.strip('_')


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
            page_html = resp.read().decode("utf-8", errors="replace")

        # 1. Search in result__a links
        links = re.findall(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"', page_html)
        for l in links:
            clean = unescape_and_clean_url(l)
            if clean and "neetprep.com/questions" in clean:
                return clean

        # 2. Search anywhere in HTML for neetprep question links
        matches = re.findall(r'href="([^"]*neetprep\.com\/questions[^"]*)"', page_html, re.IGNORECASE)
        for m in matches:
            clean = unescape_and_clean_url(m)
            if clean and "neetprep.com/questions/" in clean:
                return clean

        # 3. Search for DDG redirect links: uddg=...
        uddg_matches = re.findall(r'uddg=([^\s&"]+)', page_html, re.IGNORECASE)
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
    1. If in CHAPTER_BASE_MAP: builds chapter base URL, inspects question banks on the page
       via HTTP (or in-browser), and matches bank corresponding to user prefix.
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
            for title, _ in http_links[:12]:
                _log(f"   • {title}")
        return None, "no_matching_bank"

    # 2. Dynamic search for custom or unlisted chapters:
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

    def get_all_bank_links(self, base_url: str) -> List[Tuple[str, str]]:
        """Opens the chapter base URL in Chromium and extracts all question bank links."""
        if not self.is_active:
            if not self.start():
                return []
        try:
            self._page.goto(base_url, wait_until="domcontentloaded", timeout=25000)
            self._page.wait_for_timeout(1200)
            links = self._page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('a'))
                        .map(a => [a.innerText.trim(), a.href])
                        .filter(item => item[0].length > 0 && item[1].includes('questions/') && item[1].includes('testId='));
                }
            """)
            return [(t, u) for t, u in links if t and u]
        except Exception as ex:
            self.log_fn(f"⚠️ In-browser get_all_bank_links warning: {ex}")
            return []

    def resolve_bank_link(self, base_url: str, user_prefix: str) -> Tuple[Optional[str], Optional[str]]:
        """Opens chapter base URL in Chromium and matches question bank corresponding to user_prefix."""
        links = self.get_all_bank_links(base_url)
        return find_best_test_link(links, user_prefix)

    def fetch_graphql(self, query: str, offset: int, referer_url: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not self.is_active:
            if not self.start():
                return None, "Failed to start in-browser engine (Playwright not available)"
        try:
            if referer_url and self._page and self._page.url != referer_url:
                try:
                    self._page.goto(referer_url, wait_until="domcontentloaded", timeout=25000)
                    self._page.wait_for_timeout(800)
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
    """Detailed summary tracking question counts per JSON, skipped banks, and failure reasons."""
    def __init__(self, mode: str, prefix: str, exclude_keywords: str, target_subjects: List[str]):
        self.mode = mode
        self.prefix = prefix
        self.exclude_keywords = exclude_keywords
        self.target_subjects = target_subjects
        self.start_time = datetime.datetime.now()
        self.end_time: Optional[datetime.datetime] = None
        self.items_succeeded: List[Dict[str, Any]] = []
        self.items_failed: List[Dict[str, Any]] = []
        self.items_skipped: List[Dict[str, Any]] = []
        self.total_questions = 0

    def record_success(self, subject: str, chapter: str, bank_title: str, count: int, rel_path: str, url: str, time_taken: float):
        self.items_succeeded.append({
            "subject": subject,
            "chapter": chapter,
            "bank_title": bank_title,
            "question_count": count,
            "filename": Path(rel_path).name,
            "rel_path": rel_path,
            "url": url,
            "time_taken_seconds": round(time_taken, 2),
            "status": "SUCCESS"
        })
        self.total_questions += count

    def record_failure(self, subject: str, chapter: str, bank_title: str, reason: str, url: Optional[str] = None):
        self.items_failed.append({
            "subject": subject,
            "chapter": chapter,
            "bank_title": bank_title,
            "reason": reason,
            "url": url or "N/A",
            "status": "FAILED"
        })

    def record_skipped(self, subject: str, chapter: str, bank_title: str, exclude_rule: str, test_id: str):
        self.items_skipped.append({
            "subject": subject,
            "chapter": chapter,
            "bank_title": bank_title,
            "exclude_rule": exclude_rule,
            "test_id": test_id
        })

    def finish(self):
        self.end_time = datetime.datetime.now()

    @property
    def total_attempted(self) -> int:
        return len(self.items_succeeded) + len(self.items_failed)

    @property
    def duration_seconds(self) -> float:
        end = self.end_time or datetime.datetime.now()
        return round((end - self.start_time).total_seconds(), 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "prefix": self.prefix,
            "exclude_keywords": self.exclude_keywords,
            "target_subjects": self.target_subjects,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": self.duration_seconds,
            "total_items_attempted": self.total_attempted,
            "total_items_succeeded": len(self.items_succeeded),
            "total_items_failed": len(self.items_failed),
            "total_items_skipped": len(self.items_skipped),
            "total_questions_extracted": self.total_questions,
            "success_rate_percent": round((len(self.items_succeeded) / max(1, self.total_attempted)) * 100, 1),
            "succeeded_items": self.items_succeeded,
            "failed_items": self.items_failed,
            "skipped_items": self.items_skipped,
            # Backwards-compatibility aliases for Web Studio summary view:
            "chapters_succeeded": self.items_succeeded,
            "chapters_failed": self.items_failed,
            "total_chapters_attempted": self.total_attempted,
            "total_chapters_succeeded": len(self.items_succeeded),
            "total_chapters_failed": len(self.items_failed)
        }

    def generate_markdown(self) -> str:
        """Generates clean markdown report inside ZIP archive."""
        rate = round((len(self.items_succeeded) / max(1, self.total_attempted)) * 100, 1)
        mode_label = "Bulk: All Available Chapter Banks" if self.mode == "all_banks" else "Single Question Bank Prefix"
        lines = [
            f"# 📊 NEETprep Extraction Summary Report",
            f"",
            f"- **Execution Date**: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **Extraction Mode**: {mode_label}",
        ]
        if self.mode == "all_banks":
            lines.append(f"- **Exclude Filter**: `{self.exclude_keywords}`")
            lines.append(f"- **Banks Skipped via Exclude Filter**: ⏭️ {len(self.items_skipped)} banks")
        else:
            lines.append(f"- **Filter Prefix**: `{self.prefix}`")

        lines.extend([
            f"- **Duration**: {self.duration_seconds} seconds",
            f"- **Successfully Extracted**: ✅ {len(self.items_succeeded)} question banks",
            f"- **Failed / Missing**: ❌ {len(self.items_failed)} question banks",
            f"- **Total Questions Extracted**: 🎯 **{self.total_questions} questions**",
            f"- **Success Rate**: **{rate}%**",
            f"",
            f"---",
            f"",
            f"## 📁 Extracted Question Banks & Directory Hierarchy",
            f"",
            f"| Subject | Chapter Name | Question Bank | Questions | Output Path in ZIP |",
            f"| :--- | :--- | :--- | :---: | :--- |"
        ])

        if not self.items_succeeded:
            lines.append("| - | - | *No question banks successfully extracted* | 0 | - |")
        else:
            for item in self.items_succeeded:
                lines.append(f"| {item['subject']} | {item['chapter']} | **{item['bank_title']}** | **{item['question_count']}** | `{item['rel_path']}` |")

        if self.items_skipped:
            lines.extend([
                f"",
                f"---",
                f"",
                f"## ⏭️ Banks Skipped (Matched Exclude Filter: `{self.exclude_keywords}`)",
                f"",
                f"| Subject | Chapter Name | Skipped Question Bank | Matched Exclude Rule | Test ID |",
                f"| :--- | :--- | :--- | :--- | :---: |"
            ])
            for item in self.items_skipped:
                lines.append(f"| {item['subject']} | {item['chapter']} | {item['bank_title']} | `{item['exclude_rule']}` | `{item['test_id']}` |")

        lines.extend([
            f"",
            f"---",
            f"",
            f"## ⚠️ Failed Items / Zero Questions",
            f""
        ])

        if not self.items_failed:
            lines.append("🎉 **Zero failures! All targeted question banks were successfully extracted.**")
        else:
            lines.extend([
                f"| Subject | Chapter Name | Bank / Target | Failure Reason | URL |",
                f"| :--- | :--- | :--- | :--- | :--- |"
            ])
            for item in self.items_failed:
                lines.append(f"| {item['subject']} | {item['chapter']} | {item['bank_title']} | ❌ {item['reason']} | `{item['url']}` |")

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
        mode: str = "all_banks",
        prefix: str = "jee mains mcq [2014-2026] neetprep",
        exclude_keywords: str = "0.5, absorb, verma, jee",
        target_subjects: Optional[List[str]] = None,
        target_chapters: Optional[List[str]] = None
    ):
        self.id = job_id
        self.output_dir = output_dir
        self.mode = mode
        self.prefix = prefix
        self.exclude_keywords = exclude_keywords
        self.target_subjects = target_subjects or ["All"]
        self.target_chapters = target_chapters or []

        self.state = "idle"
        self.message = "Job initialized"
        self.done = 0
        self.total = 0
        self.active_chapter = ""
        self.active_subject = ""
        self.active_bank = ""
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

        self.summary = ExtractionSummary(
            mode=self.mode,
            prefix=self.prefix,
            exclude_keywords=self.exclude_keywords,
            target_subjects=self.target_subjects
        )

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
        self.log(f"🚀 Starting NEETprep Question Extractor Studio")
        self.log(f"⚙️ Execution Mode : {'Bulk (All Available Chapter Banks)' if self.mode == 'all_banks' else 'Single Prefix Search'}")
        if self.mode == "all_banks":
            self.log(f"🚫 Exclude Filter : \"{self.exclude_keywords}\"")
        else:
            self.log(f"🎯 Search Prefix  : \"{self.prefix}\"")
        self.log(f"📚 Target Subjects: {', '.join(self.target_subjects)}")
        self.log(f"📋 Queued Chapters: {self.total}")
        self.log(f"========================================================")

        if self.total == 0:
            self.state = "completed"
            self.message = "No matching chapters found."
            return

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

            # Prepare structured destination directory: job_work_dir / <Subject> / <Chapter>
            safe_subj = sanitize_filename(subject)
            safe_chap = sanitize_filename(chapter)
            chap_dir = job_work_dir / safe_subj / safe_chap

            if self.mode == "all_banks":
                self.log(f"\n========================================================")
                self.log(f"🚀 [{idx}/{self.total}] Chapter: [{subject}] {chapter}")

                # Find base URL
                base_url = None
                if subject in CHAPTER_BASE_MAP and chapter in CHAPTER_BASE_MAP[subject]:
                    mapping = CHAPTER_BASE_MAP[subject][chapter]
                    safe_ch_slug = re.sub(r'[^a-zA-Z0-9]', '-', chapter).strip('-')
                    base_url = f"https://www.neetprep.com/questions/{mapping['subject']}-{subject}/{mapping['chapter']}-{safe_ch_slug}"
                else:
                    # Unlisted chapter fallback: search DDG
                    clean_ch = re.sub(r'[,.\-_]', ' ', chapter).strip()
                    clean_ch = re.sub(r'\s+', ' ', clean_ch).lower()
                    found_url = search_duckduckgo_http(f"neetprep questions {clean_ch}")
                    if found_url:
                        subj_n, chap_n, _, _ = parse_neetprep_url(found_url)
                        if subj_n and chap_n:
                            base_url = f"https://www.neetprep.com/questions/{subj_n}-{subject}/{chap_n}-{clean_ch.replace(' ', '-')}"

                if not base_url:
                    err_msg = f"Could not determine chapter page URL for '{chapter}'"
                    self.log(f"❌ {err_msg}")
                    self.summary.record_failure(subject, chapter, "All Banks", err_msg)
                    self.done += 1
                    continue

                self.log(f"🌐 Inspecting Chapter Page: {base_url}")
                queued_banks, skipped_banks = get_chapter_all_banks_filtered(
                    base_url=base_url,
                    exclude_keywords=self.exclude_keywords,
                    browser_session=self.browser_session
                )

                if skipped_banks:
                    self.log(f"⏭️ Skipped {len(skipped_banks)} bank(s) matching exclude filter [{self.exclude_keywords}]:")
                    for sk in skipped_banks:
                        self.log(f"   • {sk['title']} (Rule: '{sk['exclude_rule']}', ID: {sk['test_id']})")
                        self.summary.record_skipped(subject, chapter, sk["title"], sk["exclude_rule"], sk["test_id"])

                if not queued_banks:
                    self.log(f"⚠️ No banks left to extract for '{chapter}' (all skipped or none found).")
                    self.done += 1
                    continue

                self.log(f"📋 Queued {len(queued_banks)} bank(s) to extract for '{chapter}':")
                for qb in queued_banks:
                    self.log(f"   🎯 {qb['title']} (testId: {qb['test_id']})")

                chap_dir.mkdir(parents=True, exist_ok=True)

                for b_idx, bank in enumerate(queued_banks, 1):
                    if self._cancel_requested:
                        break
                    bank_title = bank["title"]
                    self.active_bank = bank_title
                    self.message = f"[{idx}/{self.total}] {chapter} -> ({b_idx}/{len(queued_banks)}) {bank_title[:28]}"
                    self.log(f"\n   📥 [{b_idx}/{len(queued_banks)}] Extracting: \"{bank_title}\" (testId: {bank['test_id']})...")

                    b_start = time.time()
                    questions, err = fetch_chapter_questions(
                        subject_num=bank["subject_num"],
                        chapter_num=bank["chapter_num"],
                        course_num=bank["course_num"],
                        test_id=bank["test_id"],
                        referer_url=bank["url"],
                        log_fn=self.log,
                        cancel_check=lambda: self._cancel_requested,
                        browser_session=self.browser_session
                    )
                    b_duration = time.time() - b_start

                    if err:
                        self.log(f"   ❌ Error extracting bank '{bank_title}': {err}")
                        self.summary.record_failure(subject, chapter, bank_title, err, bank["url"])
                    elif not questions:
                        self.log(f"   ⚠️ Bank '{bank_title}' returned 0 questions.")
                        self.summary.record_failure(subject, chapter, bank_title, "Returned 0 questions", bank["url"])
                    else:
                        safe_title = sanitize_filename(bank_title)
                        out_filename = f"{safe_title}.json"
                        out_file_path = chap_dir / out_filename
                        with open(out_file_path, "w", encoding="utf-8") as f:
                            json.dump(questions, f, indent=2, ensure_ascii=False)

                        rel_path = f"{safe_subj}/{safe_chap}/{out_filename}"
                        self.log(f"   ✅ Saved {len(questions)} questions to `{rel_path}` ({b_duration:.1f}s)")
                        self.summary.record_success(subject, chapter, bank_title, len(questions), rel_path, bank["url"], b_duration)

            else:
                # Single prefix search mode
                self.log(f"\n========================================================")
                self.log(f"🚀 [{idx}/{self.total}] Chapter: [{subject}] {chapter}")
                self.log(f"🔎 Searching For  : Prefix=\"{self.prefix}\" + Chapter=\"{chapter}\"")

                start_t = time.time()
                target_url, source = resolve_chapter_url(
                    subject, chapter, self.prefix, log_fn=self.log, browser_session=self.browser_session
                )

                if not target_url:
                    if source == "no_matching_bank":
                        err_msg = f"No question bank matching prefix '{self.prefix}' exists for this chapter"
                    else:
                        err_msg = f"No valid NEETprep question bank found for prefix '{self.prefix}'"
                    self.log(f"⚠️ {err_msg}")
                    self.summary.record_failure(subject, chapter, self.prefix, err_msg)
                    self.done += 1
                    continue

                subject_num, chapter_num, course_num, test_id = parse_neetprep_url(target_url)
                if not subject_num or not chapter_num:
                    err_msg = f"Failed to parse subject/chapter IDs from URL: {target_url}"
                    self.log(f"❌ {err_msg}")
                    self.summary.record_failure(subject, chapter, self.prefix, err_msg, target_url)
                    self.done += 1
                    continue

                self.log(f"⏳ Querying NEETprep GraphQL (Subject: {subject_num}, Topic: {chapter_num}, Course: {course_num}, Test: {test_id})...")
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
                    self.summary.record_failure(subject, chapter, self.prefix, err, target_url)
                elif not questions:
                    err_msg = "Received 0 questions from GraphQL API"
                    self.log(f"⚠️ {err_msg}")
                    self.summary.record_failure(subject, chapter, self.prefix, err_msg, target_url)
                else:
                    chap_dir.mkdir(parents=True, exist_ok=True)
                    safe_prefix = safe_slug(self.prefix)
                    file_name = f"{safe_prefix}.json"
                    file_path = chap_dir / file_name

                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(questions, f, indent=2, ensure_ascii=False)

                    rel_path = f"{safe_subj}/{safe_chap}/{file_name}"
                    self.log(f"✅ Saved {len(questions)} questions to `{rel_path}` in {time_taken:.1f}s")
                    self.summary.record_success(subject, chapter, self.prefix, len(questions), rel_path, target_url, time_taken)

            self.done += 1

        # Finish and Create Summary Documents
        self.summary.finish()
        summary_dict = self.summary.to_dict()
        markdown_report = self.summary.generate_markdown()

        summary_json_path = job_work_dir / "extraction_summary.json"
        with open(summary_json_path, "w", encoding="utf-8") as f:
            json.dump(summary_dict, f, indent=2, ensure_ascii=False)

        summary_md_path = job_work_dir / "README_SUMMARY.md"
        with open(summary_md_path, "w", encoding="utf-8") as f:
            f.write(markdown_report)

        # Bundle into Downloadable ZIP Archive preserving folder structure
        now_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        if self.mode == "all_banks":
            zip_filename = f"NEETprep_AllBanks_{now_str}.zip"
        else:
            safe_prefix = safe_slug(self.prefix) or "neetprep"
            zip_filename = f"{safe_prefix}_Extracted_{now_str}.zip"
        zip_filepath = self.output_dir / zip_filename

        self.log(f"\n📦 Packaging all chapters and summary reports into `{zip_filename}`...")
        with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as z:
            for item in job_work_dir.rglob("*"):
                if item.is_file():
                    z.write(item, arcname=item.relative_to(job_work_dir).as_posix())

        self.output_zip_path = zip_filepath

        try:
            shutil.rmtree(job_work_dir, ignore_errors=True)
        except Exception:
            pass

        # Final Summary Output
        self.log("\n" + "=" * 60)
        self.log("📊 EXTRACTION SUMMARY:")
        self.log(f"   • Total Items Attempted   : {self.summary.total_attempted}")
        self.log(f"   • Successfully Extracted  : {len(self.summary.items_succeeded)} banks ({self.summary.total_questions} questions)")
        if self.summary.items_skipped:
            self.log(f"   • Skipped (Exclude Filter): {len(self.summary.items_skipped)} banks")
        self.log(f"   • Failed Items            : {len(self.summary.items_failed)}")
        self.log(f"   • Total Time Taken        : {self.summary.duration_seconds}s")
        self.log(f"   • ZIP Archive             : {zip_filepath.name}")
        if self.summary.items_failed:
            self.log("   ❌ FAILED ITEMS LIST:")
            for failed in self.summary.items_failed:
                self.log(f"      - [{failed['subject']}] {failed['chapter']} - {failed['bank_title']} (Reason: {failed['reason']})")
        self.log("=" * 60)

        if not self._cancel_requested:
            self.state = "completed"
            self.message = f"Finished! Extracted {self.summary.total_questions} questions across {len(self.summary.items_succeeded)} banks."


# =============================================================================
# CLI Standalone Entrypoint
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="NEETprep DuckDuckGo + GraphQL Extractor")
    parser.add_argument("--mode", type=str, default="all_banks", choices=["all_banks", "single_prefix"], help="Extraction mode (all_banks or single_prefix)")
    parser.add_argument("--exclude", type=str, default="0.5, absorb, verma, jee", help="Comma-separated bank titles/keywords to exclude in all_banks mode")
    parser.add_argument("--prefix", type=str, default="jee mains mcq [2014-2026] neetprep", help="Search prefix in single_prefix mode")
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
        mode=args.mode,
        prefix=args.prefix,
        exclude_keywords=args.exclude,
        target_subjects=target_subjects,
        target_chapters=target_chapters
    )

    print("🚀 Launching NEETprep Extractor in CLI mode...")
    job._execute()


if __name__ == "__main__":
    main()
