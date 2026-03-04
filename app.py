import hashlib
import hmac
import io
import os
from datetime import datetime, timedelta
from pathlib import Path
from time import time
from textwrap import wrap

import extra_streamlit_components as stx
import mysql.connector
import streamlit as st
from mysql.connector import Error
from PIL import Image, ImageDraw, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

st.set_page_config(page_title="AI Resume Builder", page_icon="🧠", layout="wide")

BASE_DIR = Path(__file__).parent
TEMPLATE_DIR = BASE_DIR / "templates"
AUTH_COOKIE_NAME = "arb_auth"
AUTH_COOKIE_TTL_DAYS = 7
AUTH_SECRET = os.getenv("APP_AUTH_SECRET", "change_this_secret_in_env")


def get_db_config() -> dict:
    return {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", "admin"),
        "database": os.getenv("MYSQL_DATABASE", "ai_resume_builder"),
    }


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def open_mysql_connection(server_level: bool = False):
    config = get_db_config().copy()
    if server_level:
        config.pop("database", None)
    return mysql.connector.connect(**config)


def init_memory_store() -> None:
    st.session_state.setdefault("memory_users", {})
    st.session_state.setdefault("memory_next_user_id", 1)
    st.session_state.setdefault("memory_next_resume_id", 1)
    st.session_state.setdefault("memory_resumes", [])


def initialize_database() -> bool:
    try:
        server_conn = open_mysql_connection(server_level=True)
        server_cursor = server_conn.cursor()
        database_name = get_db_config()["database"]
        server_cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database_name}`")
        server_conn.commit()
        server_cursor.close()
        server_conn.close()

        conn = open_mysql_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS resumes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                full_name VARCHAR(255) NOT NULL,
                education TEXT NOT NULL,
                skills TEXT NOT NULL,
                projects TEXT NOT NULL,
                template_name VARCHAR(100) NOT NULL,
                user_prompt TEXT NOT NULL,
                generated_text LONGTEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Error:
        return False


def register_user(username: str, password: str) -> tuple[bool, str]:
    if st.session_state.db_enabled:
        try:
            conn = open_mysql_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, hash_password(password)),
            )
            conn.commit()
            cursor.close()
            conn.close()
            return True, "Account created successfully."
        except Error as exc:
            if "Duplicate" in str(exc):
                return False, "Username already exists."
            return False, "Database error while creating account."

    users = st.session_state.memory_users
    if username in users:
        return False, "Username already exists."

    user_id = st.session_state.memory_next_user_id
    st.session_state.memory_next_user_id += 1
    users[username] = {"id": user_id, "username": username, "password_hash": hash_password(password)}
    return True, "Account created successfully (memory mode)."


def login_user(username: str, password: str):
    password_hash = hash_password(password)

    if st.session_state.db_enabled:
        try:
            conn = open_mysql_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT id, username FROM users WHERE username=%s AND password_hash=%s",
                (username, password_hash),
            )
            user = cursor.fetchone()
            cursor.close()
            conn.close()
            return user
        except Error:
            return None

    user = st.session_state.memory_users.get(username)
    if not user or user["password_hash"] != password_hash:
        return None
    return {"id": user["id"], "username": user["username"]}


def save_resume(user_id: int, form_data: dict, generated_text: str) -> None:
    if st.session_state.db_enabled:
        try:
            conn = open_mysql_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO resumes (user_id, full_name, education, skills, projects, template_name, user_prompt, generated_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    form_data["full_name"],
                    form_data["education"],
                    form_data["skills"],
                    form_data["projects"],
                    form_data["template_name"],
                    form_data["user_prompt"],
                    generated_text,
                ),
            )
            conn.commit()
            cursor.close()
            conn.close()
            return
        except Error:
            pass

    st.session_state.memory_resumes.append(
        {
            "id": st.session_state.memory_next_resume_id,
            "user_id": user_id,
            "template_name": form_data["template_name"],
            "generated_text": generated_text,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    st.session_state.memory_next_resume_id += 1


def fetch_resume_history(user_id: int):
    if st.session_state.db_enabled:
        try:
            conn = open_mysql_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, template_name, generated_text, created_at
                FROM resumes
                WHERE user_id=%s
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            return rows
        except Error:
            return []

    rows = [item for item in st.session_state.memory_resumes if item["user_id"] == user_id]
    rows.reverse()
    return rows[:20]


def delete_resume(user_id: int, resume_id: int) -> bool:
    if st.session_state.db_enabled:
        try:
            conn = open_mysql_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM resumes WHERE id=%s AND user_id=%s", (resume_id, user_id))
            conn.commit()
            deleted = cursor.rowcount > 0
            cursor.close()
            conn.close()
            return deleted
        except Error:
            return False

    resumes = st.session_state.memory_resumes
    for index, item in enumerate(resumes):
        if item.get("id") == resume_id and item.get("user_id") == user_id:
            resumes.pop(index)
            return True
    return False


def fetch_user_by_id(user_id: int):
    if st.session_state.db_enabled:
        try:
            conn = open_mysql_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id, username FROM users WHERE id=%s", (user_id,))
            user = cursor.fetchone()
            cursor.close()
            conn.close()
            return user
        except Error:
            return None

    for user in st.session_state.memory_users.values():
        if user["id"] == user_id:
            return {"id": user["id"], "username": user["username"]}
    return None


def load_templates() -> dict[str, str]:
    templates: dict[str, str] = {}
    if not TEMPLATE_DIR.exists():
        return templates

    for file_path in sorted(TEMPLATE_DIR.glob("*.md")):
        label = file_path.stem.replace("_", " ").title()
        templates[label] = file_path.read_text(encoding="utf-8").strip()
    return templates


def to_bullets(text: str) -> list[str]:
    lines = [line.strip(" -\t") for line in (text or "").splitlines() if line.strip()]
    return lines


def _build_single_prompt(form_data: dict, template_body: str, ai_mode: str) -> str:
    mode_guidance = (
        "Prioritize ATS keywords, clarity, and measurable impact. Keep style conservative and recruiter-friendly."
        if ai_mode == "Strict ATS"
        else "Allow more compelling phrasing while staying professional and concise. Improve storytelling and readability."
    )

    return f"""
You are an expert resume writer and ATS optimization assistant.
Generate one final professional resume from this candidate data.
Generation mode: {ai_mode}
Mode objective: {mode_guidance}

Strict output rules:
- Use plain section titles without markdown symbols.
- Keep concise, impact-focused wording.
- Improve grammar and clarity.
- Do not output placeholders like "Not provided".
- If information is missing, skip that line/section.
- Keep sections in this order when content exists:
  PROFILE SUMMARY, EDUCATION, SKILLS, PROJECTS, CERTIFICATES/INTERN, SOFT SKILLS, PROFESSIONAL EXPERIENCE, ACHIEVEMENTS, INTERESTS, LANGUAGES
- Start with candidate name on first line, professional headline on second line, and contact details on third line (pipe-separated).

Candidate data:
Full Name: {form_data.get('full_name', '').strip()}
Profile Headline: {form_data.get('profile_headline', '').strip()}
Email: {form_data.get('email', '').strip()}
Phone: {form_data.get('phone', '').strip()}
Location: {form_data.get('location', '').strip()}
LinkedIn: {form_data.get('linkedin', '').strip()}
GitHub: {form_data.get('github', '').strip()}
Portfolio: {form_data.get('portfolio', '').strip()}
Career Objective: {form_data.get('career_objective', '').strip()}
Education: {form_data.get('education', '').strip()}
Skills: {form_data.get('skills', '').strip()}
Projects: {form_data.get('projects', '').strip()}
Certificates: {form_data.get('certificates', '').strip()}
Soft Skills: {form_data.get('soft_skills', '').strip()}
Professional Experience: {form_data.get('professional_experience', '').strip()}
Achievements: {form_data.get('achievements', '').strip()}
Interests: {form_data.get('interests', '').strip()}
Languages: {form_data.get('languages', '').strip()}
Target Role Template: {form_data.get('template_name', '').strip()}

Template guidance:
{template_body[:1500] if template_body else 'Standard ATS-friendly professional resume style.'}

Return only the final resume text.
""".strip()


@st.cache_resource(show_spinner=False)
def _get_ai_generator():
    from transformers import pipeline

    model_name = os.getenv("RESUME_AI_MODEL", "google/flan-t5-small")
    return pipeline("text2text-generation", model=model_name, tokenizer=model_name)


def _score_resume_quality(text: str, form_data: dict) -> int:
    score = 0
    normalized = text.lower()
    required_sections = [
        "profile summary",
        "education",
        "skills",
        "projects",
    ]
    optional_sections = [
        "certificates/intern",
        "soft skills",
        "professional experience",
        "achievements",
        "languages",
    ]

    for section in required_sections:
        if section in normalized:
            score += 8

    for section in optional_sections:
        if section in normalized:
            score += 3

    for keyword in to_bullets(form_data.get("skills", ""))[:8]:
        if keyword.lower() in normalized:
            score += 2

    word_count = len(text.split())
    if 180 <= word_count <= 520:
        score += 15
    elif 120 <= word_count < 180 or 520 < word_count <= 700:
        score += 6

    if "not provided" in normalized:
        score -= 20

    return score


def _generate_resume_with_ai(prompt: str, form_data: dict, ai_mode: str, ai_quality: str) -> str | None:
    try:
        generator = _get_ai_generator()
        quality_config = {
            "Fast": {"strict_candidates": 1, "creative_candidates": 1, "max_new_tokens": 420},
            "Balanced": {"strict_candidates": 1, "creative_candidates": 3, "max_new_tokens": 650},
            "Best": {"strict_candidates": 2, "creative_candidates": 5, "max_new_tokens": 820},
        }
        config = quality_config.get(ai_quality, quality_config["Balanced"])

        candidate_count = (
            config["strict_candidates"] if ai_mode == "Strict ATS" else config["creative_candidates"]
        )
        candidates: list[str] = []

        for _ in range(candidate_count):
            result = generator(
                prompt,
                max_new_tokens=config["max_new_tokens"],
                do_sample=(ai_mode != "Strict ATS"),
                temperature=0.2 if ai_mode == "Strict ATS" else 0.85,
                top_p=0.85 if ai_mode == "Strict ATS" else 0.95,
                repetition_penalty=1.15,
            )
            if not result:
                continue
            generated_text = result[0].get("generated_text", "").strip()
            if generated_text:
                candidates.append(generated_text)

        if not candidates:
            return None

        best_text = max(candidates, key=lambda item: _score_resume_quality(item, form_data))
        return best_text
    except Exception:
        return None


def _generate_resume_fallback(form_data: dict) -> str:
    skills = to_bullets(form_data["skills"])
    projects = to_bullets(form_data["projects"])
    experience = to_bullets(form_data.get("professional_experience", ""))
    interests = to_bullets(form_data.get("interests", ""))
    achievements = to_bullets(form_data.get("achievements", ""))
    certificates = to_bullets(form_data.get("certificates", ""))
    languages = to_bullets(form_data.get("languages", ""))
    soft_skills = to_bullets(form_data.get("soft_skills", ""))

    objective_line = form_data.get("career_objective", "").strip()
    objective_text = objective_line or f"Targeting {form_data['template_name']} opportunities with a strong delivery focus."
    candidate_name = form_data.get("full_name", "").strip() or "Candidate"
    email_text = form_data.get("email", "").strip()
    phone_text = form_data.get("phone", "").strip()
    location_text = form_data.get("location", "").strip()
    linkedin_text = form_data.get("linkedin", "").strip()
    github_text = form_data.get("github", "").strip()
    portfolio_text = form_data.get("portfolio", "").strip()
    education_text = form_data.get("education", "").strip()
    template_name = form_data.get("template_name", "Professional")
    profile_headline = form_data.get("profile_headline", "").strip()
    if profile_headline:
        tagline_line = profile_headline
    else:
        tagline_parts = [education_text] if education_text else []
        if skills:
            tagline_parts.append(" | ".join(skills[:2]))
        if not tagline_parts:
            tagline_parts.append(f"{template_name} Candidate")
        tagline_line = " | ".join(tagline_parts[:2])

    contact_items = [item for item in [email_text, phone_text, location_text, linkedin_text, github_text, portfolio_text] if item]
    contact_line = " | ".join(contact_items)

    sections: list[str] = [candidate_name]
    sections.append(tagline_line)
    if contact_line:
        sections.append(contact_line)
    sections.append("")

    def add_section(title: str, items: list[str]) -> None:
        if not items:
            return
        sections.append(title)
        sections.extend(items)
        sections.append("")

    add_section(
        "PROFILE SUMMARY",
        [
            f"Entry-level candidate targeting {template_name} opportunities.",
            f"Career Objective: {objective_text}",
        ],
    )

    add_section(
        "EDUCATION",
        [education_text] if education_text else ["Education details available on request."],
    )

    if skills:
        add_section("SKILLS", [f"{item}" for item in skills])
    if projects:
        add_section("PROJECTS", [f"{item}" for item in projects])
    if certificates:
        add_section("CERTIFICATES/INTERN", [f"{item}" for item in certificates])
    if soft_skills:
        add_section("SOFT SKILLS", [f"{item}" for item in soft_skills])
    if experience:
        add_section("PROFESSIONAL EXPERIENCE", [f"{item}" for item in experience])
    if achievements:
        add_section("ACHIEVEMENTS", [f"{item}" for item in achievements])
    if interests:
        add_section("INTERESTS", [f"{item}" for item in interests])
    if languages:
        add_section("LANGUAGES", [f"{item}" for item in languages])

    sections.append("Generated using fallback mode.")
    return "\n".join(sections).strip()


def generate_resume_text(form_data: dict, template_body: str) -> str:
    ai_mode = form_data.get("ai_mode", "Strict ATS")
    ai_quality = form_data.get("ai_quality", "Balanced")
    prompt = _build_single_prompt(form_data, template_body, ai_mode)
    ai_output = _generate_resume_with_ai(prompt, form_data, ai_mode, ai_quality)
    if ai_output:
        return ai_output
    return _generate_resume_fallback(form_data)


def build_resume_pdf(content: str, profile_image_bytes: bytes | None = None) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    left_margin = 50
    right_margin = 50
    y = height - 50
    line_height = 15

    lines = [line.strip() for line in content.splitlines()]
    section_titles = {
        "PROFILE SUMMARY",
        "EDUCATION",
        "SKILLS",
        "PROJECTS",
        "CERTIFICATES/INTERN",
        "SOFT SKILLS",
        "PROFESSIONAL EXPERIENCE",
        "ACHIEVEMENTS",
        "INTERESTS",
        "LANGUAGES",
    }

    def is_contact_line(text: str) -> bool:
        return (
            "@" in text
            or any(char.isdigit() for char in text)
            or "linkedin" in text.lower()
            or "github" in text.lower()
            or text.count("|") >= 1
        )

    header_lines: list[str] = []
    content_start_index = 0
    for index, line in enumerate(lines):
        if not line:
            continue
        if line in section_titles:
            content_start_index = index
            break
        header_lines.append(line)
        content_start_index = index + 1

    name_line = header_lines[0] if header_lines else "Candidate"
    tagline_line = ""
    contact_line = ""
    if len(header_lines) >= 2:
        if is_contact_line(header_lines[1]):
            contact_line = header_lines[1]
        else:
            tagline_line = header_lines[1]
    if len(header_lines) >= 3:
        contact_line = header_lines[2]

    if profile_image_bytes:
        try:
            image = ImageReader(io.BytesIO(profile_image_bytes))
            image_width = 95
            image_height = 115
            image_x = width - right_margin - image_width
            image_y = y - image_height + 20
            pdf.drawImage(
                image,
                image_x,
                image_y,
                width=image_width,
                height=image_height,
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception:
            pass

    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(left_margin, y, name_line)

    if tagline_line:
        name_width = pdf.stringWidth(name_line, "Helvetica-Bold", 22)
        tagline_x = left_margin + name_width + 14
        if tagline_x < width - right_margin - 80:
            pdf.setFont("Helvetica-Oblique", 17)
            pdf.drawString(tagline_x, y + 1, tagline_line)
            y -= 14
        else:
            y -= 22
            pdf.setFont("Helvetica-Oblique", 16)
            pdf.drawString(left_margin, y, tagline_line)
    y -= 10
    pdf.line(left_margin, y, width - right_margin, y)
    y -= 18

    if contact_line:
        contact_parts = [part.strip() for part in contact_line.split("|") if part.strip()]
        icons = ["✉", "☎", "📍", "in", "🔗", "🔗"]
        x = left_margin
        for idx, part in enumerate(contact_parts):
            icon = icons[idx] if idx < len(icons) else "•"
            chunk = f"{icon}  {part}"
            chunk_width = pdf.stringWidth(chunk, "Helvetica", 11)
            if x + chunk_width > width - right_margin:
                y -= 16
                x = left_margin
            pdf.setFont("Helvetica", 11)
            pdf.drawString(x, y, chunk)
            x += chunk_width + 16
        y -= 18

    for raw_line in lines[content_start_index:]:
        line = raw_line.strip()
        if not line:
            y -= 8
            continue

        if line in section_titles:
            pdf.setFont("Helvetica-Bold", 12)
            pdf.drawString(left_margin, y, line)
            y -= 6
            pdf.line(left_margin, y, width - right_margin, y)
            y -= 18
            continue

        if all(char == "-" for char in line):
            continue

        display_line = line

        wrapped_lines = wrap(display_line, width=104) or [" "]

        for wrapped in wrapped_lines:
            if y < 60:
                pdf.showPage()
                y = height - 50
            pdf.setFont("Helvetica", 11)
            pdf.drawString(left_margin, y, wrapped)
            y -= line_height

    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def make_circular_image_bytes(image_bytes: bytes, size: int) -> bytes | None:
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        resampling = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        fitted = ImageOps.fit(image, (size, size), method=resampling)

        mask = Image.new("L", (size, size), 0)
        drawer = ImageDraw.Draw(mask)
        drawer.ellipse((0, 0, size - 1, size - 1), fill=255)

        output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        output.paste(fitted, (0, 0), mask)

        result_buffer = io.BytesIO()
        output.save(result_buffer, format="PNG")
        result_buffer.seek(0)
        return result_buffer.getvalue()
    except Exception:
        return None


def get_cookie_manager():
    if "cookie_manager" not in st.session_state:
        st.session_state.cookie_manager = stx.CookieManager()
    return st.session_state.cookie_manager


def create_auth_token(user: dict) -> str:
    expires_at = int(time()) + AUTH_COOKIE_TTL_DAYS * 24 * 60 * 60
    payload = f"{user['id']}:{user['username']}:{expires_at}"
    signature = hmac.new(AUTH_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_auth_token(token: str):
    try:
        user_id_str, username, expires_at_str, signature = token.split(":", 3)
        payload = f"{user_id_str}:{username}:{expires_at_str}"
        expected_signature = hmac.new(
            AUTH_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            return None
        if int(expires_at_str) < int(time()):
            return None
        return {"id": int(user_id_str), "username": username}
    except Exception:
        return None


def try_restore_session_from_cookie() -> None:
    if st.session_state.logged_in:
        return

    try:
        cookie_manager = get_cookie_manager()
        token = cookie_manager.get(AUTH_COOKIE_NAME)
        if not token:
            return

        token_user = verify_auth_token(token)
        if not token_user:
            cookie_manager.delete(AUTH_COOKIE_NAME)
            return

        db_user = fetch_user_by_id(token_user["id"])
        if not db_user or db_user["username"] != token_user["username"]:
            cookie_manager.delete(AUTH_COOKIE_NAME)
            return

        st.session_state.logged_in = True
        st.session_state.user = db_user
    except Exception:
        return


def reset_auth_state() -> None:
    try:
        get_cookie_manager().delete(AUTH_COOKIE_NAME)
    except Exception:
        pass
    st.session_state.logged_in = False
    st.session_state.user = None
    st.session_state.generated_resume = ""


def initialize_state() -> None:
    init_memory_store()
    st.session_state.setdefault("db_enabled", initialize_database())
    st.session_state.setdefault("logged_in", False)
    st.session_state.setdefault("user", None)
    st.session_state.setdefault("generated_resume", "")
    st.session_state.setdefault("generated_profile_image_bytes", None)
    st.session_state.setdefault("full_name", "")
    st.session_state.setdefault("profile_headline", "")
    st.session_state.setdefault("email", "")
    st.session_state.setdefault("phone", "")
    st.session_state.setdefault("location", "")
    st.session_state.setdefault("linkedin", "")
    st.session_state.setdefault("github", "")
    st.session_state.setdefault("portfolio", "")
    st.session_state.setdefault("career_objective", "")
    st.session_state.setdefault("professional_experience", "")
    st.session_state.setdefault("education", "")
    st.session_state.setdefault("skills", "")
    st.session_state.setdefault("interests", "")
    st.session_state.setdefault("achievements", "")
    st.session_state.setdefault("projects", "")
    st.session_state.setdefault("certificates", "")
    st.session_state.setdefault("languages", "")
    st.session_state.setdefault("soft_skills", "")
    st.session_state.setdefault("profile_image_size", 140)
    st.session_state.setdefault("selected_cv_design", "Classic Resume")
    st.session_state.setdefault("selected_template", "")
    st.session_state.setdefault("selected_ai_mode", "Strict ATS")
    st.session_state.setdefault("selected_ai_quality", "Balanced")
    st.session_state.setdefault("selected_page", "Resume Builder")
    st.session_state.setdefault("auth_mode", "signin")


def render_profile_page() -> None:
    st.subheader("Profile")
    st.caption("Manage your personal details once and reuse them while generating resumes.")

    profile_col_1, profile_col_2 = st.columns(2)
    with profile_col_1:
        st.text_input("Full Name", key="full_name")
        st.text_input("Email", key="email")
        st.text_input("Phone", key="phone")
        st.text_input("Location", key="location")
    with profile_col_2:
        st.text_input("Professional Headline", key="profile_headline", placeholder="B.Tech CSE Student | Java Full-Stack Developer")
        st.text_input("LinkedIn", key="linkedin", placeholder="linkedin.com/in/your-profile")
        st.text_input("GitHub", key="github", placeholder="github.com/username")
        st.text_input("Portfolio / Coding Profile", key="portfolio", placeholder="CodeChef / LeetCode / Portfolio URL")

    if st.button("Save Profile", use_container_width=True):
        st.success("Profile saved. Go to Resume Builder page to generate.")


def render_about_page() -> None:
    st.subheader("About")
    st.markdown("**AI Resume Builder**")
    st.write("Generate professional resumes using AI with profile-based inputs and downloadable TXT/PDF output.")

    st.markdown("### Features")
    st.write("- AI resume generation (Strict ATS / Creative modes)")
    st.write("- Fast / Balanced / Best quality options")
    st.write("- Profile page for reusable personal details")
    st.write("- Resume history with open and delete actions")
    st.write("- PDF export with styled header and optional profile image")

    st.markdown("### Tech Stack")
    st.write("- Streamlit, Python, MySQL")
    st.write("- Transformers (local model inference)")
    st.write("- ReportLab + Pillow for PDF rendering")

    if st.button("Back to Resume Builder", use_container_width=True):
        st.session_state.selected_page = "Resume Builder"
        st.rerun()


def render_auth() -> None:
    st.markdown(
        """
        <style>
            .main .block-container {max-width: 1200px; padding-top: 2rem;}
            div[data-testid="stForm"] {padding: 1rem 1rem 0.5rem 1rem; border: 1px solid rgba(128,128,128,0.25); border-radius: 12px;}
            .auth-title {text-align: center; margin-bottom: 1rem;}
            div[data-testid="stForm"] {transition: transform 0.2s ease, box-shadow 0.2s ease;}
            div[data-testid="stForm"]:hover {transform: translateY(-2px); box-shadow: 0 8px 20px rgba(0,0,0,0.18);}
            div.stButton > button {transition: transform 0.2s ease, box-shadow 0.2s ease;}
            div.stButton > button:hover {transform: translateY(-2px); box-shadow: 0 6px 14px rgba(0,0,0,0.2);}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("<h1 class='auth-title'>AI Resume Builder</h1>", unsafe_allow_html=True)
    center_left, center, center_right = st.columns([1, 1.2, 1])

    with center:
        if st.session_state.auth_mode == "signin":
            st.subheader("Login")
            with st.form("sign_in_form", clear_on_submit=False):
                username = st.text_input("Username", key="login_username")
                password = st.text_input("Password", type="password", key="login_password")
                submitted = st.form_submit_button("Login", use_container_width=True)

                if submitted:
                    if not username or not password:
                        st.error("Username and password are required.")
                    else:
                        user = login_user(username.strip(), password)
                        if user:
                            st.session_state.logged_in = True
                            st.session_state.user = user
                            try:
                                token = create_auth_token(user)
                                get_cookie_manager().set(
                                    AUTH_COOKIE_NAME,
                                    token,
                                    expires_at=datetime.utcnow() + timedelta(days=AUTH_COOKIE_TTL_DAYS),
                                )
                            except Exception:
                                pass
                            st.success("Login successful.")
                            st.rerun()
                        else:
                            st.error("Invalid credentials.")

            if st.button("New user? Click to open Sign Up", use_container_width=True):
                st.session_state.auth_mode = "signup"
                st.rerun()
        else:
            st.subheader("Sign Up")
            with st.form("sign_up_form", clear_on_submit=True):
                username = st.text_input("Create Username", key="register_username")
                password = st.text_input("Create Password", type="password", key="register_password")
                confirm_password = st.text_input("Confirm Password", type="password", key="register_confirm")
                submitted = st.form_submit_button("Create Account", use_container_width=True)

                if submitted:
                    if not username or not password:
                        st.error("Username and password are required.")
                    elif password != confirm_password:
                        st.error("Passwords do not match.")
                    elif len(password) < 6:
                        st.error("Password must be at least 6 characters.")
                    else:
                        ok, message = register_user(username.strip(), password)
                        if ok:
                            st.success(message)
                            st.session_state.auth_mode = "signin"
                        else:
                            st.error(message)

            if st.button("Already have an account? Back to Login", use_container_width=True):
                st.session_state.auth_mode = "signin"
                st.rerun()

    st.markdown(
        """
        <div style='
            position: fixed;
            left: 0;
            right: 0;
            bottom: 8px;
            text-align: center;
            opacity: 0.85;
            font-size: 14px;
            pointer-events: none;
            z-index: 999;
        '>made by praveenilla 2026</div>
        """,
        unsafe_allow_html=True,
    )


def render_app() -> None:
    templates = load_templates()
    template_names = list(templates.keys())

    if template_names and not st.session_state.selected_template:
        st.session_state.selected_template = template_names[0]

    st.markdown(
        """
        <style>
            section[data-testid="stSidebar"] div.stButton > button,
            section[data-testid="stSidebar"] button {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                outline: none !important;
                border-radius: 0 !important;
                text-align: left !important;
                padding: 0.1rem 0 !important;
                min-height: auto !important;
                color: inherit !important;
            }
            section[data-testid="stSidebar"] div.stButton > button:hover,
            section[data-testid="stSidebar"] div.stButton > button:focus,
            section[data-testid="stSidebar"] div.stButton > button:active,
            section[data-testid="stSidebar"] button:hover,
            section[data-testid="stSidebar"] button:focus,
            section[data-testid="stSidebar"] button:active {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                outline: none !important;
                text-decoration: underline;
                transform: none !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown(f"**Logged in as: {st.session_state.user['username']}**")
        if st.button("Logout", use_container_width=True):
            reset_auth_state()
            st.rerun()

        st.divider()
        page_options = ["Resume Builder", "Profile", "About"]
        if st.session_state.selected_page not in page_options:
            st.session_state.selected_page = "Resume Builder"
        selected_page_value = st.selectbox(
            "Page",
            page_options,
            index=page_options.index(st.session_state.selected_page),
        )
        if selected_page_value != st.session_state.selected_page:
            st.session_state.selected_page = selected_page_value

        st.divider()
        st.markdown("Recent resumes")
        history = fetch_resume_history(st.session_state.user["id"])
        resume_count = len(history)
        if history:
            for item in history[:8]:
                timestamp = item["created_at"]
                if hasattr(timestamp, "strftime"):
                    timestamp = timestamp.strftime("%Y-%m-%d %H:%M")
                item_label = f"{timestamp} • {item['template_name']}"
                row_col_1, row_col_2 = st.columns([5, 1])
                with row_col_1:
                    if st.button(item_label, key=f"open_resume_{item['id']}", use_container_width=True):
                        st.session_state.generated_resume = item.get("generated_text", "")
                        st.session_state.generated_profile_image_bytes = None
                        st.rerun()
                with row_col_2:
                    if st.button("x", key=f"delete_resume_{item['id']}", help="Delete this resume"):
                        deleted = delete_resume(st.session_state.user["id"], item["id"])
                        if deleted:
                            if st.session_state.generated_resume == item.get("generated_text", ""):
                                st.session_state.generated_resume = ""
                            st.rerun()
                        else:
                            st.error("Failed to delete resume")
        else:
            st.caption("No resumes yet.")

        st.divider()
        st.markdown("Profile summary")
        sidebar_name = st.session_state.full_name.strip() or st.session_state.user["username"]
        sidebar_email = st.session_state.email.strip() or "Not added"
        st.caption(f"Name: {sidebar_name}")
        st.caption(f"Email: {sidebar_email}")
        st.caption(f"Resumes generated: {resume_count}")

        st.divider()
        st.caption("About")
        if st.button("Open About Page", key="open_about_page", use_container_width=True):
            st.session_state.selected_page = "About"
            st.rerun()

    if st.session_state.selected_page == "Profile":
        render_profile_page()
        return
    if st.session_state.selected_page == "About":
        render_about_page()
        return

    st.markdown(
        """
        <style>
            div[data-testid="stTextInput"] input {
                height: 46px;
                transition: transform 0.18s ease, box-shadow 0.18s ease;
            }
            div[data-testid="stTextArea"] textarea {
                height: 46px !important;
                min-height: 46px !important;
                max-height: 46px !important;
                resize: none !important;
                transition: transform 0.18s ease, box-shadow 0.18s ease;
            }
            div[data-testid="stFileUploaderDropzone"] {
                min-height: 46px !important;
                padding-top: 0.35rem !important;
                padding-bottom: 0.35rem !important;
                transition: transform 0.18s ease, box-shadow 0.18s ease;
            }
            div[data-baseweb="select"] > div {transition: transform 0.18s ease, box-shadow 0.18s ease;}
            div[data-testid="stTextInput"] input:focus,
            div[data-testid="stTextArea"] textarea:focus,
            div[data-baseweb="select"] > div:hover,
            div[data-testid="stFileUploaderDropzone"]:hover {
                transform: translateY(-2px);
                box-shadow: 0 6px 14px rgba(0,0,0,0.16);
            }
            div.stButton > button {transition: transform 0.2s ease, box-shadow 0.2s ease;}
            div.stButton > button:hover {transform: translateY(-2px); box-shadow: 0 8px 16px rgba(0,0,0,0.22);}
        </style>
        """,
        unsafe_allow_html=True,
    )

    row_1_col_1, row_1_col_2 = st.columns(2)
    with row_1_col_1:
        st.text_input("Full Name", key="full_name")
    with row_1_col_2:
        st.text_input("Email", key="email")

    row_2_col_1, row_2_col_2 = st.columns(2)
    with row_2_col_1:
        st.text_input("Phone", key="phone")
    with row_2_col_2:
        st.text_input("Location", key="location")

    row_3_col_1, row_3_col_2 = st.columns(2)
    with row_3_col_1:
        st.text_area("Career Objective", key="career_objective", height=46)
    with row_3_col_2:
        st.text_input("Education", key="education")

    row_4_col_1, row_4_col_2 = st.columns(2)
    with row_4_col_1:
        st.text_area("Skills (one per line)", key="skills", height=46)
    with row_4_col_2:
        st.text_area("Projects (one per line)", key="projects", height=46)

    row_5_col_1, row_5_col_2 = st.columns(2)
    with row_5_col_1:
        st.text_area("Certificates (one per line)", key="certificates", height=46)
    with row_5_col_2:
        st.text_area("Soft Skills (one per line)", key="soft_skills", height=46)

    row_6_col_1, row_6_col_2 = st.columns(2)
    with row_6_col_1:
        st.empty()
    with row_6_col_2:
        uploaded_profile_image = st.file_uploader(
            "Profile Image",
            type=["png", "jpg", "jpeg"],
            key="profile_image",
            accept_multiple_files=False,
        )
        if uploaded_profile_image is not None:
            circular_image = make_circular_image_bytes(
                uploaded_profile_image.getvalue(),
                int(st.session_state.profile_image_size),
            )
            if circular_image is not None:
                st.image(circular_image, caption="Uploaded profile image (circular 360°)")
            else:
                st.image(
                    uploaded_profile_image,
                    caption="Uploaded profile image",
                    width=int(st.session_state.profile_image_size),
                )

    row_7_col_1, row_7_col_2 = st.columns(2)
    with row_7_col_1:
        st.slider(
            "Profile Image Size (px)",
            min_value=80,
            max_value=320,
            step=5,
            key="profile_image_size",
        )
    with row_7_col_2:
        st.selectbox(
            "CV Design Template",
            [
                "Classic Resume",
                "Blue Classic Cover",
                "Elite One-Column",
                "Curved Modern CV",
            ],
            key="selected_cv_design",
        )

    mode_col_1, mode_col_2 = st.columns(2)
    with mode_col_1:
        st.selectbox(
            "AI Mode",
            ["Strict ATS", "Creative"],
            key="selected_ai_mode",
        )
    with mode_col_2:
        st.selectbox(
            "AI Quality",
            ["Fast", "Balanced", "Best"],
            key="selected_ai_quality",
        )
    st.caption(
        "Fast = quickest generation, Balanced = good speed/quality, Best = strongest output (slower)."
    )

    if template_names:
        st.selectbox("Resume Profile Template", template_names, key="selected_template")
    else:
        st.warning("No template files found in templates folder.")
        st.session_state.selected_template = "General"

    if st.button("Generate Resume", use_container_width=True, type="primary"):
        profile_image_bytes = uploaded_profile_image.getvalue() if uploaded_profile_image is not None else None
        form_data = {
            "full_name": st.session_state.full_name.strip(),
            "profile_headline": st.session_state.profile_headline.strip(),
            "email": st.session_state.email.strip(),
            "phone": st.session_state.phone.strip(),
            "location": st.session_state.location.strip(),
            "linkedin": st.session_state.linkedin.strip(),
            "github": st.session_state.github.strip(),
            "portfolio": st.session_state.portfolio.strip(),
            "career_objective": st.session_state.career_objective.strip(),
            "professional_experience": "",
            "education": st.session_state.education.strip(),
            "skills": st.session_state.skills,
            "interests": "",
            "achievements": "",
            "projects": st.session_state.projects,
            "certificates": st.session_state.certificates,
            "languages": "",
            "soft_skills": st.session_state.soft_skills,
            "has_profile_image": profile_image_bytes is not None,
            "cv_design": st.session_state.selected_cv_design,
            "template_name": st.session_state.selected_template,
            "ai_mode": st.session_state.selected_ai_mode,
            "ai_quality": st.session_state.selected_ai_quality,
            "user_prompt": f"Create a professional resume for {st.session_state.selected_template}",
        }

        template_body = templates.get(form_data["template_name"], "")
        generated_text = generate_resume_text(form_data, template_body)
        st.session_state.generated_resume = generated_text
        st.session_state.generated_profile_image_bytes = profile_image_bytes
        save_resume(st.session_state.user["id"], form_data, generated_text)
        st.success("Resume generated successfully.")
        st.rerun()

    if st.session_state.generated_resume:
        st.text_area(
            "Generated Resume",
            value=st.session_state.generated_resume,
            height=420,
            disabled=True,
        )

        pdf_bytes = build_resume_pdf(
            st.session_state.generated_resume,
            st.session_state.get("generated_profile_image_bytes"),
        )
        download_col_1, download_col_2 = st.columns(2)
        with download_col_1:
            st.download_button(
                "Download TXT",
                data=st.session_state.generated_resume,
                file_name="resume.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with download_col_2:
            st.download_button(
                "Download PDF",
                data=pdf_bytes,
                file_name="resume.pdf",
                mime="application/pdf",
                use_container_width=True,
            )


def main() -> None:
    initialize_state()
    try_restore_session_from_cookie()

    if not st.session_state.db_enabled:
        st.warning("MySQL connection unavailable. Running in memory mode for this session.")

    if not st.session_state.logged_in:
        render_auth()
        return

    st.markdown("<h1 style='text-align:center;'>AI Resume Builder</h1>", unsafe_allow_html=True)
    render_app()


if __name__ == "__main__":
    main()
