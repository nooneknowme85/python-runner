"""StudyAura / StudySpark Extractor for Python Runner & Quizzy.

Discovers batches, subjects, chapters, DPPs, and Test Series from StudyAura.
Extracts questions, answer keys, solutions, images, and videos.
Formats everything into 100% Quizzy-compatible JSON (Test Cafe & Question Cafe).
Thread-safe background runner with live log streaming and ZIP packaging.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# =============================================================================
# Helper Utilities
# =============================================================================

def sanitize_filename(name: str) -> str:
    """Sanitizes names for files and folders."""
    return re.sub(r'[\/\\?%*:|"<>#]', '_', (name or "untitled")).strip()


def extract_batch_id(input_str: str) -> str:
    """Extracts 24-character hex batch ID from raw string or full URL."""
    clean = (input_str or "").strip()
    m = re.search(r'batches/([a-f0-9]{24})', clean, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    m2 = re.search(r'\b([a-f0-9]{24})\b', clean, re.IGNORECASE)
    if m2:
        return m2.group(1).lower()
    return clean


def get_subject_display_name(s: Any) -> str:
    if not s:
        return "General"
    if isinstance(s, str):
        return s.strip()
    if isinstance(s, dict):
        teachers = s.get("teacherIds")
        teacher_subj = teachers[0].get("subject") if isinstance(teachers, list) and teachers and isinstance(teachers[0], dict) else None
        raw = s.get("subject") or s.get("name") or s.get("title") or teacher_subj or s.get("slug") or ""
        return str(raw).strip() or "Subject"
    return "Subject"


def get_clean_subject_folder(s: Any) -> str:
    if not s:
        return "General"
    if isinstance(s, dict):
        teachers = s.get("teacherIds")
        if isinstance(teachers, list) and teachers and isinstance(teachers[0], dict) and teachers[0].get("subject"):
            return str(teachers[0]["subject"]).strip()
    raw = s if isinstance(s, str) else get_subject_display_name(s)
    clean = re.sub(r'\s+(by|By)\s+.*$', '', raw, flags=re.IGNORECASE)
    clean = re.sub(r'\s+(OG|GOAT|Pro|Legend|Batch)$', '', clean, flags=re.IGNORECASE).strip()
    return clean or raw or "General"


def resolve_full_image_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    clean = str(url).strip().replace("&amp;", "&")
    if not clean or clean.lower() in ("null", "undefined", "none"):
        return None
    if clean.startswith("//"):
        return "https:" + clean
    if clean.startswith(("http://", "https://")):
        return clean
    return "https://d2bps9p1kiy4ka.cloudfront.net/" + clean.lstrip("/")


def resolve_inline_html_images(html: Optional[str]) -> str:
    if not html or not isinstance(html, str):
        return ""
    def _repl(m):
        pre, src, post = m.group(1), m.group(2), m.group(3)
        return f'<img {pre}src="{resolve_full_image_url(src)}"{post}>'
    return re.sub(r'<img\s+([^>]*?)src=["\']([^"\']+)["\']([^>]*?)>', _repl, html, flags=re.IGNORECASE)


def clean_html_to_text(html_content: Optional[str]) -> str:
    if not html_content:
        return ""
    text = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', str(html_content), flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(div|p|ul|ol|li|h[1-6])>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<li>', '• ', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()


def resolve_youtube_video(q: dict, idx: int) -> Optional[dict]:
    raw_v_url = str(q.get("videoUrl") or "")
    m = re.search(r'(?:youtu\.be\/|youtube\.com\/(?:embed\/|v\/|watch\?v=|watch\?.+&v=))([\w-]{11})', raw_v_url, re.IGNORECASE)
    video_id = None
    if m:
        video_id = m.group(1)
    elif q.get("urlType") == 'youtube' and q.get("videoId") and re.match(r'^[\w-]{11}$', q["videoId"]):
        video_id = q["videoId"]

    if video_id:
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        embed_url = f"https://www.youtube-nocookie.com/embed/{video_id}"
        q_num = q.get("questionNumber") or (idx + 1)
        return {
            "url": watch_url,
            "embed_url": embed_url,
            "title": f"Question {q_num} Video Solution"
        }
    return None


def http_get_json(url: str, timeout: int = 20) -> Optional[dict]:
    """Safe HTTP GET request with standard headers (bypasses bot filters)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://studyaura.online/",
        "Origin": "https://studyaura.online"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content)
    except Exception:
        return None


# =============================================================================
# Quizzy Schema Converter
# =============================================================================

def convert_spark_to_quizzy(raw_data: dict, meta: dict) -> dict:
    data_obj = raw_data.get("data") if isinstance(raw_data.get("data"), dict) else raw_data
    questions = data_obj.get("questions") if isinstance(data_obj.get("questions"), list) else raw_data.get("questions", [])
    if not isinstance(questions, list):
        questions = []

    first_q = questions[0] if questions else {}
    pos_mark = first_q.get("positiveMarks", 4)
    neg_mark = -abs(first_q.get("negativeMarks", 1)) if first_q.get("negativeMarks") is not None else -1
    test_name = data_obj.get("testName") or meta.get("name") or "Quizzy Test"
    total_dur = data_obj.get("maxDuration") or meta.get("duration") or max(15, len(questions) * 3)

    test_id = meta.get("id") or str(uuid.uuid4())

    def is_jee_adv(name: str) -> bool:
        if not name:
            return False
        s = str(name).lower()
        if "mains" in s or "neet" in s:
            return False
        if "advance" in s:
            return True
        if re.search(r'paper\s*[:\-_]?\s*[12]\b', s):
            return True
        return False

    is_adv = is_jee_adv(test_name) or is_jee_adv(meta.get("name", "")) or is_jee_adv(meta.get("batchName", ""))
    adv_marking = "📌 Pattern: JEE Advanced Marking Scheme\n• Single MCQ: +3, -1\n• Multi-Correct: +4 Full | Partial (+3, +2, +1) | -2 Penalty\n• Numerical: +4, 0 (No negative marking)"
    raw_desc = (data_obj.get("description") or meta.get("description") or "").strip()

    quizzy_obj: Dict[str, Any] = {
        "id": test_id,
        "name": test_name,
        "displayName": test_name,
        "duration": total_dur,
        "marking": {"correct": pos_mark, "incorrect": neg_mark},
        "sections": []
    }

    if is_adv:
        quizzy_obj["description"] = f"{raw_desc}\n\n{adv_marking}" if raw_desc else f"{test_name}\n\n{adv_marking}"
    elif raw_desc:
        quizzy_obj["description"] = raw_desc

    # Syllabus
    raw_syl = data_obj.get("syllabus") or meta.get("syllabus")
    if isinstance(raw_syl, dict):
        raw_syl = raw_syl.get("en") or raw_syl.get("content") or ""
    if raw_syl and isinstance(raw_syl, str) and raw_syl.strip():
        clean_syl = clean_html_to_text(raw_syl)
        if clean_syl:
            quizzy_obj["syllabus"] = {
                "enabled": True,
                "type": "text",
                "content": clean_syl,
                "buttonLabel": "📄 View Syllabus"
            }

    sec_map: Dict[str, List[dict]] = {}
    for idx, q in enumerate(questions):
        sec_name = q.get("subjectName") or meta.get("realSubjectName") or meta.get("subjectName") or "General"
        sec_name = get_clean_subject_folder(sec_name)
        if sec_name not in sec_map:
            sec_map[sec_name] = []

        # Question text
        q_text = q.get("questionText") or ""
        if not q_text and isinstance(q.get("questionTexts"), dict):
            q_text = q["questionTexts"].get("en") or next(iter(q["questionTexts"].values()), "")
        q_text = resolve_inline_html_images(q_text)

        # Question image
        raw_img = q.get("questionImage")
        if not raw_img and isinstance(q.get("questionImages"), dict):
            raw_img = q["questionImages"].get("en") or next(iter(q["questionImages"].values()), None)
        main_img = resolve_full_image_url(raw_img)
        if not main_img and q_text:
            m_img = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', q_text, re.IGNORECASE)
            if m_img:
                main_img = resolve_full_image_url(m_img.group(1))

        # Type detection
        raw_type = str(q.get("type") or "").strip().lower()
        has_correct_text = q.get("correctText") is not None and str(q.get("correctText")).strip() != ""
        has_numeric_ans = q.get("numericAnswer") is not None and str(q.get("numericAnswer")).strip() != ""
        is_integer = raw_type in ("numeric", "numerical", "integer") or has_correct_text or has_numeric_ans
        is_multi = raw_type in ("multiple", "multi", "multi_mcq")

        # Options
        raw_opts = []
        if isinstance(q.get("optionsByLang"), dict):
            lang_key = "en" if "en" in q["optionsByLang"] else (next(iter(q["optionsByLang"].keys())) if q["optionsByLang"] else None)
            raw_opts = q["optionsByLang"].get(lang_key, []) if lang_key else []
        elif isinstance(q.get("options"), list):
            raw_opts = q["options"]

        options: List[str] = []
        option_images: List[Optional[str]] = []
        has_any_opt_img = False
        correct_indices: List[int] = []

        corr_ids: List[str] = []
        if isinstance(q.get("correctOptionIds"), list):
            corr_ids = [str(x).strip() for x in q["correctOptionIds"]]
        elif q.get("correctOptionId") is not None:
            corr_ids = [s.strip() for s in str(q["correctOptionId"]).split(",") if s.strip()]

        if not is_integer:
            for o_idx, opt in enumerate(raw_opts):
                opt_text = opt.get("text") or opt.get("title") or "" if isinstance(opt, dict) else str(opt)
                opt_img = opt.get("image") or opt.get("imageUrl") or opt.get("image_url") if isinstance(opt, dict) else None
                if not opt_img and isinstance(opt, dict) and isinstance(opt.get("images"), dict):
                    opt_img = opt["images"].get("en")
                if not opt_img and opt_text:
                    m_opt_img = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', opt_text, re.IGNORECASE)
                    if m_opt_img:
                        opt_img = m_opt_img.group(1)

                resolved_opt_img = resolve_full_image_url(opt_img)
                if resolved_opt_img:
                    has_any_opt_img = True
                option_images.append(resolved_opt_img)

                clean_text = resolve_inline_html_images(opt_text)
                if not clean_text and resolved_opt_img:
                    clean_text = f"({chr(65 + o_idx)})"
                options.append(clean_text)

                if isinstance(opt, dict):
                    opt_id = str(opt.get("id") or opt.get("_id") or "")
                    opt_letter = chr(65 + o_idx)
                    is_matched = (opt_id and opt_id in corr_ids) or (opt_letter in corr_ids) or (opt_text and opt_text.strip() in corr_ids) or bool(opt.get("isCorrect"))
                    if is_matched:
                        correct_indices.append(o_idx)

        q_item: Dict[str, Any] = {
            "id": str(q.get("questionId") or q.get("_id") or uuid.uuid4()),
            "question": q_text,
            "image_url": main_img,
            "images": [main_img] if main_img else []
        }

        raw_ans = q.get("correctText") if q.get("correctText") is not None else (q.get("numericAnswer") if q.get("numericAnswer") is not None else q.get("answer", ""))
        raw_ans_upper = str(raw_ans).strip().upper()

        is_bonus = bool(
            q.get("isBonus") or q.get("is_bonus") or q.get("bonus") or q.get("isBonusQuestion") or q.get("isDropped") or q.get("dropped") or
            raw_ans_upper == "BONUS" or "BONUS" in raw_ans_upper or raw_ans_upper == "ALL" or
            (not is_integer and len(options) > 1 and len(correct_indices) == len(options)) or
            (not is_integer and len(raw_opts) > 1 and len(corr_ids) == len(raw_opts))
        )

        if is_bonus:
            q_item["is_bonus"] = True

        if is_integer:
            q_item["type"] = "integer"
            q_item["options"] = None
            q_item["correct"] = None
            q_item["answer"] = str(raw_ans).strip()
        elif is_bonus:
            q_item["type"] = "multi_mcq"
            q_item["options"] = options
            q_item["correct"] = list(range(len(options)))
            if has_any_opt_img:
                q_item["optionImages"] = option_images
        elif is_multi:
            q_item["type"] = "multi_mcq"
            q_item["options"] = options
            q_item["correct"] = correct_indices if correct_indices else [0]
            if has_any_opt_img:
                q_item["optionImages"] = option_images
        else:
            q_item["type"] = "mcq"
            q_item["options"] = options
            q_item["correct"] = correct_indices[0] if correct_indices else 0
            if has_any_opt_img:
                q_item["optionImages"] = option_images

        # Solutions
        raw_sol_text = q.get("solutionText") or ""
        if not raw_sol_text and isinstance(q.get("solutionTexts"), dict):
            raw_sol_text = q["solutionTexts"].get("en") or next(iter(q["solutionTexts"].values()), "")
        if raw_sol_text:
            q_item["explanation"] = resolve_inline_html_images(raw_sol_text)

        raw_sol_img = q.get("solutionImage")
        if not raw_sol_img and isinstance(q.get("solutionImages"), dict):
            raw_sol_img = q["solutionImages"].get("en") or next(iter(q["solutionImages"].values()), None)
        sol_img = resolve_full_image_url(raw_sol_img)
        if sol_img:
            q_item["explanation_image"] = sol_img

        if is_bonus:
            bonus_notice = "⚠️ **Bonus Question / All Options Correct**\n*This question was officially awarded as BONUS. If your test engine does not calculate bonus automatically, please manually add the positive marks to your total obtained marks.*"
            q_item["explanation"] = f"{bonus_notice}\n\n---\n{q_item['explanation']}" if q_item.get("explanation") else bonus_notice

        # Video Solution
        yt_video = resolve_youtube_video(q, idx)
        if yt_video:
            q_item["video"] = yt_video
            q_item["video_url"] = yt_video["url"]

        sec_map[sec_name].append(q_item)

    for sec_name, sec_qs in sec_map.items():
        quizzy_obj["sections"].append({"name": sec_name, "icon": "", "questions": sec_qs})

    return quizzy_obj


# =============================================================================
# Discovery API Functions
# =============================================================================

def discover_batch_info(batch_id_or_url: str) -> Dict[str, Any]:
    """Fetches batch metadata, subjects, and test counts."""
    batch_id = extract_batch_id(batch_id_or_url)
    if not batch_id or len(batch_id) != 24:
        return {"success": False, "error": f"Invalid 24-character Batch ID: '{batch_id}'"}

    urls = [
        f"https://studyaura.online/api/penpencil/v3/batches/{batch_id}/details",
        f"https://studyaura.online/api/penpencil/batch-service/v2/batches/{batch_id}/details"
    ]
    batch_data = None
    for u in urls:
        resp = http_get_json(u)
        if resp and resp.get("data"):
            batch_data = resp["data"]
            break

    if not batch_data:
        return {"success": False, "error": f"Batch not found on StudyAura for ID: {batch_id}"}

    batch_name = batch_data.get("name") or "StudyAura Batch"
    raw_subjects = batch_data.get("subjects") or []

    subjects_list = []
    for s in raw_subjects:
        s_id = s.get("_id") or s.get("id") or s.get("slug")
        s_title = get_subject_display_name(s)
        s_clean = get_clean_subject_folder(s)
        subjects_list.append({
            "id": s_id,
            "name": s_title,
            "folder": s_clean
        })

    # Discover tests count
    test_url = f"https://studyaura.online/api/penpencil/v3/test-service/tests/batch/{batch_id}?page=1&limit=50"
    test_resp = http_get_json(test_url)
    test_count = 0
    if test_resp and isinstance(test_resp.get("data"), list):
        test_count = len(test_resp["data"])

    return {
        "success": True,
        "batch_id": batch_id,
        "batch_name": batch_name,
        "subjects": subjects_list,
        "test_count": test_count,
        "fee": batch_data.get("fee", 0),
        "is_free": batch_data.get("isFree", False)
    }


def fetch_subject_chapters(batch_id: str, subject_id: str) -> List[dict]:
    """Fetches all chapter topics for a subject."""
    chapters = []
    seen_ids = set()
    page = 1
    while page <= 15:
        url = f"https://studyaura.online/api/penpencil/batch-service/v1/batch-tags/{batch_id}/subject/{subject_id}/topics?page={page}&batchTagType=UNITS&limit=20"
        resp = http_get_json(url)
        if not resp or not resp.get("data"):
            break
        items = resp["data"]
        new_items = [t for t in items if (t.get("_id") or t.get("tagId")) not in seen_ids]
        if not new_items:
            break
        for t in new_items:
            seen_ids.add(t.get("_id") or t.get("tagId"))
            chapters.append(t)
        page += 1
        time.sleep(0.08)
    return chapters


def fetch_chapter_dpps(batch_id: str, subject_id: str, chapter_id: str, subject_name: str, chapter_name: str) -> List[dict]:
    """Fetches all DPP quizzes for a specific chapter."""
    url = f"https://studyaura.online/api/penpencil/v3/test-service/tests/new-dpp-list?page=1&batchId={batch_id}&batchSubjectId={subject_id}&chapterId={chapter_id}&dppType=ALL&limit=50"
    resp = http_get_json(url)
    items = []
    if not resp or not resp.get("data"):
        return items

    for d in resp["data"]:
        details = d.get("dppQuizDetails") or {}
        test = details.get("test") or {}
        test_id = test.get("_id")
        if not test_id or len(test_id) != 24:
            continue
        name = (test.get("name") or test.get("title") or d.get("title") or d.get("name") or f"DPP_{test_id}").strip()
        duration = test.get("maxDuration") or 30

        start_dpp_url = f"https://studyaura.online/api/penpencil/v3/test-service/tests/dpp-quiz/{test_id}/start-dpp"
        items.append({
            "is_test": False,
            "id": test_id,
            "name": name,
            "duration": duration,
            "subject_name": subject_name,
            "chapter_name": chapter_name,
            "fetch_url": start_dpp_url
        })
    return items


def fetch_batch_tests(batch_id: str) -> List[dict]:
    """Fetches all Full Mock Tests & Test Series for a batch."""
    tests = []
    seen_ids = set()
    page = 1
    while page <= 10:
        url = f"https://studyaura.online/api/penpencil/v3/test-service/tests/batch/{batch_id}?page={page}&limit=50"
        resp = http_get_json(url)
        if not resp or not resp.get("data"):
            break
        items = resp["data"]
        new_items = [t for t in items if t.get("_id") not in seen_ids]
        if not new_items:
            break
        for t in new_items:
            seen_ids.add(t.get("_id"))
            t_id = t.get("_id")
            name = (t.get("name") or t.get("title") or f"Test_{t_id}").strip()
            dur = t.get("maxDuration") or 180
            start_test_url = f"https://studyaura.online/api/penpencil/v3/test-service/tests/{t_id}/start-test"
            tests.append({
                "is_test": True,
                "id": t_id,
                "name": name,
                "duration": dur,
                "subject_name": "Test Series",
                "chapter_name": "Tests",
                "category_name": "Test Series",
                "fetch_url": start_test_url,
                "syllabus": t.get("syllabus")
            })
        page += 1
        time.sleep(0.08)
    return tests


# =============================================================================
# Background Job Runner
# =============================================================================

class StudyAuraJob:
    """Thread-safe background extraction job for StudyAura DPPs and Tests."""

    def __init__(
        self,
        batch_input: str,
        extract_mode: str = "all",  # "dpp", "tests", "all"
        selected_subjects: Optional[List[str]] = None,
        output_dir: Optional[Path] = None
    ):
        self.id = uuid.uuid4().hex[:10]
        self.batch_input = (batch_input or "").strip()
        self.batch_id = extract_batch_id(self.batch_input)
        self.extract_mode = (extract_mode or "all").lower().strip()
        self.selected_subjects = set(selected_subjects or [])

        self.output_dir = output_dir or (Path(__file__).parent / "workspace" / "studyaura_outputs")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.state = "queued"  # queued, running, finished, stopped, error
        self.message = "Job queued..."
        self.done = 0
        self.total = 1
        self.active_item = ""
        self.stop_requested = False

        self.logs: List[Dict[str, str]] = []
        self.failed_tracker: List[Dict[str, Any]] = []
        self.zip_files: List[Dict[str, Any]] = []
        self.json_files: List[Dict[str, Any]] = []
        self.collected_count = 0

        self.batch_name = "StudyAura Batch"
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None

    def log(self, msg: str, level: str = "info"):
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        entry = {"time": now_str, "level": level, "msg": msg}
        self.logs.append(entry)
        if len(self.logs) > 1200:
            self.logs = self.logs[-1200:]
        print(f"[{now_str}] [{level.upper()}] {msg}")

    def progress(self, done: int, total: int, msg: str, active_item: str = ""):
        self.done = done
        self.total = max(total, 1)
        self.message = msg
        if active_item:
            self.active_item = active_item

    def stop(self):
        self.stop_requested = True
        self.log("⏹️ Stop requested by user. Terminating extraction gracefully...", level="warning")
        self.message = "Stopping job..."

    def run(self):
        self.state = "running"
        self.start_time = time.time()
        self.log(f"🚀 Starting StudyAura extraction job [{self.id}]")
        self.log(f"Batch Identifier: {self.batch_id} | Mode: {self.extract_mode.upper()}")
        self.progress(0, 10, "Discovering batch information...")

        try:
            # 1. Fetch batch details
            info = discover_batch_info(self.batch_id)
            if not info.get("success"):
                raise RuntimeError(info.get("error", "Failed to discover batch."))

            self.batch_name = info["batch_name"]
            self.log(f"✅ Discovered Batch: '{self.batch_name}' ({len(info['subjects'])} subjects)")

            queue: List[dict] = []

            # 2. Discover DPPs
            if self.extract_mode in ("dpp", "all"):
                self.log("🔍 Scanning subjects & chapters for DPPs...")
                subjects_to_scan = info["subjects"]
                if self.selected_subjects:
                    subjects_to_scan = [s for s in subjects_to_scan if s["id"] in self.selected_subjects or s["name"] in self.selected_subjects]

                for s in subjects_to_scan:
                    if self.stop_requested:
                        break
                    s_id = s["id"]
                    s_name = s["name"]
                    s_clean = s["folder"]
                    chaps = fetch_subject_chapters(self.batch_id, s_id)
                    self.log(f"  • {s_name}: found {len(chaps)} chapters")

                    for ch in chaps:
                        if self.stop_requested:
                            break
                        ch_id = ch.get("_id") or ch.get("tagId")
                        ch_name = ch.get("name") or "General"
                        dpps = fetch_chapter_dpps(self.batch_id, s_id, ch_id, s_clean, ch_name)
                        for d in dpps:
                            d["real_subject_name"] = s_clean
                            d["batch_name"] = self.batch_name
                            queue.append(d)

                self.log(f"📥 Discovered {len(queue)} DPPs for download.")

            # 3. Discover Test Series
            if self.extract_mode in ("tests", "all"):
                if not self.stop_requested:
                    self.log("🔍 Scanning full test series & mock tests...")
                    b_tests = fetch_batch_tests(self.batch_id)
                    for t in b_tests:
                        t["real_subject_name"] = "Test Series"
                        t["batch_name"] = self.batch_name
                        queue.append(t)
                    self.log(f"📥 Discovered {len(b_tests)} full test series papers.")

            if not queue:
                self.log("⚠️ No DPPs or Tests found for this batch and selection.", level="warning")
                self.state = "finished"
                self.progress(1, 1, "Completed — 0 items discovered.")
                return

            self.log(f"🚀 Queued total {len(queue)} quizzes/tests to extract! Starting download loop...")
            total_items = len(queue)
            collected_data: Dict[str, dict] = {}
            temp_batch_dir = self.output_dir / f"temp_{self.id}"
            temp_batch_dir.mkdir(parents=True, exist_ok=True)

            for i, item in enumerate(queue):
                if self.stop_requested:
                    self.log("⏹️ Process stopped by user.", level="warning")
                    break

                q_name = item["name"]
                q_id = item["id"]
                self.progress(i + 1, total_items, f"Extracting [{i+1}/{total_items}]: {q_name}", active_item=q_name)

                success = False
                for attempt in range(1, 4):
                    if self.stop_requested:
                        break
                    try:
                        raw_json = http_get_json(item["fetch_url"], timeout=25)
                        if not raw_json or not raw_json.get("success") or not raw_json.get("data"):
                            raise RuntimeError("Invalid response structure or no questions")

                        quizzy_data = convert_spark_to_quizzy(raw_json, item)
                        q_count = sum(len(s.get("questions", [])) for s in quizzy_data.get("sections", []))
                        if q_count == 0:
                            raise RuntimeError("Test parsed with 0 questions")

                        collected_data[q_id] = {
                            "item": item,
                            "quizzy": quizzy_data
                        }

                        # Save individual JSON to structured temp directory
                        if item.get("is_test"):
                            rel_folder = sanitize_filename(item.get("category_name") or "Test Series")
                            target_dir = temp_batch_dir / rel_folder
                            target_file = target_dir / f"{sanitize_filename(q_name)}.json"
                        else:
                            sub_folder = sanitize_filename(item.get("real_subject_name") or "General")
                            chap_folder = sanitize_filename(item.get("chapter_name") or "General")
                            target_dir = temp_batch_dir / sub_folder / chap_folder
                            target_file = target_dir / f"{sanitize_filename(q_name)}.json"

                        target_dir.mkdir(parents=True, exist_ok=True)
                        with open(target_file, "w", encoding="utf-8") as f:
                            json.dump(quizzy_data, f, indent=2, ensure_ascii=False)

                        self.json_files.append({
                            "filename": target_file.name,
                            "relative_path": str(target_file.relative_to(self.output_dir)).replace("\\", "/"),
                            "question_count": q_count,
                            "size_kb": round(target_file.stat().st_size / 1024, 1)
                        })

                        self.log(f"[{i+1}/{total_items}] ✅ Done: {q_name} ({q_count} Qs in {len(quizzy_data.get('sections', []))} sections)")
                        success = True
                        break

                    except Exception as err:
                        if attempt < 3 and not self.stop_requested:
                            time.sleep(1.5 * attempt)
                        else:
                            self.failed_tracker.append({
                                "id": q_id,
                                "name": q_name,
                                "subject": item.get("real_subject_name", "General"),
                                "chapter": item.get("chapter_name", "General"),
                                "error": str(err)
                            })
                            self.log(f"[{i+1}/{total_items}] ❌ Failed: {q_name} ({err})", level="error")

                time.sleep(0.12)

            self.collected_count = len(collected_data)

            # 4. Packaging ZIP archive
            if any(temp_batch_dir.iterdir()):
                timestamp = int(time.time())
                safe_name = sanitize_filename(self.batch_name)
                zip_filename = f"StudyAura_{safe_name}_{timestamp}.zip"
                zip_path = self.output_dir / zip_filename

                self.log(f"📦 Packaging {len(self.json_files)} Quizzy JSONs into '{zip_filename}'...")
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                    for root, _, files in os.walk(temp_batch_dir):
                        for file in files:
                            full_fp = Path(root) / file
                            arc_name = full_fp.relative_to(temp_batch_dir).as_posix()
                            zipf.write(full_fp, arc_name)

                shutil.rmtree(temp_batch_dir, ignore_errors=True)

                zip_size_mb = round(zip_path.stat().st_size / (1024 * 1024), 2)
                self.zip_files.append({
                    "filename": zip_filename,
                    "relative_path": zip_filename,
                    "size_mb": zip_size_mb,
                    "count": len(self.json_files)
                })
                self.log(f"🎉 ZIP created successfully: {zip_filename} ({zip_size_mb} MB)")

            if self.stop_requested:
                self.state = "stopped"
                self.message = f"Stopped. Saved {self.collected_count} items."
            else:
                self.state = "finished"
                self.message = f"Completed! Successfully extracted {self.collected_count} quizzes."

            self.log(f"🏁 StudyAura Extraction finished! Total extracted: {self.collected_count} | Failures: {len(self.failed_tracker)}")

        except Exception as e:
            self.state = "error"
            self.message = f"Job Error: {e}"
            self.log(f"💥 Fatal error during StudyAura extraction: {e}", level="error")
        finally:
            self.end_time = time.time()


# =============================================================================
# Standalone CLI Entry Point
# =============================================================================

def main():
    print("=======================================================")
    print("           STUDYAURA QUIZZY EXTRACTION SUITE           ")
    print("=======================================================")
    batch_input = input("Enter StudyAura Batch ID or URL (e.g., 698ad3519549b300a5e1cc6a): ").strip()
    if not batch_input:
        print("❌ Error: Batch ID or URL cannot be empty.")
        return

    print("\nChoose Extraction Mode:")
    print("  1. 📚 DPPs Only (Chapterwise Daily Practice Problems)")
    print("  2. 🧪 Test Series Only (Full Mock Tests & AITS)")
    print("  3. 🚀 Extract Everything (All DPPs + All Tests)")
    choice = input("Enter choice (1/2/3) [default: 3]: ").strip() or "3"

    mode_map = {"1": "dpp", "2": "tests", "3": "all"}
    mode = mode_map.get(choice, "all")

    job = StudyAuraJob(batch_input=batch_input, extract_mode=mode)
    job.run()

    if job.zip_files:
        print("\n=======================================================")
        print("✅ EXTRACTION COMPLETE!")
        for z in job.zip_files:
            print(f"📦 ZIP Archive: {job.output_dir / z['filename']} ({z['size_mb']} MB)")
        print("=======================================================\n")
    else:
        print("\n⚠️ No archives created. Check logs above.")


if __name__ == "__main__":
    main()
