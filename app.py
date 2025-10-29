import os
from io import BytesIO
from datetime import datetime
from flask import Flask, render_template, request, send_file, url_for, redirect, flash, send_from_directory
from werkzeug.utils import secure_filename
import requests
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from PIL import Image

# --- Config ---
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXT = {
    "png", "jpg", "jpeg", "gif", "bmp",   # images
    "mp4", "mov", "webm", "mkv", "avi",   # videos
    "pdf", "txt", "log", "zip"            # others
}

# GitHub integration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # required
GITHUB_REPO = os.getenv("GITHUB_REPO")    # e.g. "username/repo"

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.secret_key = os.getenv("FLASK_SECRET", "devsecret")


# --- Helper functions ---
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def is_image(filename):
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in {"png", "jpg", "jpeg", "gif", "bmp"}

def add_logo(c):
    """Draw Addverb logo on the top-left corner of each page."""
    logo_path = os.path.join("static", "AddverbImage.jpeg")
    if os.path.exists(logo_path):
        logo_width = 1.7 * inch
        logo_height = 0.8 * inch
        c.drawImage(
            logo_path,
            x=40,
            y=A4[1] - 80,
            width=logo_width,
            height=logo_height,
            preserveAspectRatio=True,
            mask='auto'
        )


# --- Routes ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=False)


@app.route("/generate", methods=["POST"])
def generate():
    # --- Get form data ---
    site_name = request.form.get("site_name", "").strip()
    date_val = request.form.get("date", "").strip()
    heading = request.form.get("heading", "").strip()
    description = request.form.get("description", "").strip()
    rca_by = request.form.get("rca_by", "").strip()

    if not site_name or not date_val or not heading:
        flash("Site name, date, and heading are required.", "error")
        return redirect("/")

    # --- Handle uploads ---
    uploaded = request.files.getlist("files")
    saved_files = []  # (filename, is_image, url)

    for f in uploaded:
        if f and f.filename and allowed_file(f.filename):
            safe = secure_filename(f.filename)
            base, ext = os.path.splitext(safe)
            unique_name = f"{base}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}{ext}"
            path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
            f.save(path)
            url = url_for("uploaded_file", filename=unique_name, _external=True)
            saved_files.append((unique_name, is_image(unique_name), url))

    # --- GitHub issue creation ---
    issue_link = None
    if GITHUB_TOKEN and GITHUB_REPO:
        issue_title = f"{site_name} - {date_val} - {heading}"
        body_lines = [
            f"**Site:** {site_name}",
            f"**Date:** {date_val}",
            f"**RCA Heading:** {heading}",
            f"**RCA By:** {rca_by}",
            "\n**Description:**\n",
            description,
            "\n**Attachments:**\n"
        ]
        for fname, is_img, url in saved_files:
            if is_img:
                body_lines.append(f"![{fname}]({url})")
            else:
                body_lines.append(f"- [{fname}]({url})")
        body = "\n\n".join(body_lines)
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
        r = requests.post(f"https://api.github.com/repos/{GITHUB_REPO}/issues",
                          json={"title": issue_title, "body": body}, headers=headers)
        issue_link = r.json().get("html_url") if r.status_code in (200, 201) else f"GitHub issue creation failed ({r.status_code})."
    else:
        issue_link = "GitHub token or repo not configured."

    # --- PDF generation ---
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    width, height = A4
    margin = 50
    y = height - margin

    add_logo(c)
    y -= 90

    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, y, "RCA Report")
    y -= 30

    c.setFont("Helvetica", 11)

    def line(label, value):
        nonlocal y
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margin, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(margin + 120, y, value)
        y -= 18

    line("Site Name:", site_name)
    line("Date:", date_val)
    line("Heading:", heading)
    line("RCA By:", rca_by)
    y -= 10

    # --- Description ---
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Description:")
    y -= 14
    c.setFont("Helvetica", 10)
    text = c.beginText(margin, y)
    max_width = width - 2 * margin
    for paragraph in description.splitlines():
        words, line_txt = paragraph.split(" "), ""
        for w in words:
            test = (line_txt + " " + w).strip()
            if c.stringWidth(test, "Helvetica", 10) <= max_width:
                line_txt = test
            else:
                text.textLine(line_txt)
                line_txt = w
        text.textLine(line_txt)
        text.textLine("")
    c.drawText(text)
    y = text.getY() - 20

    # --- Embed images ---
    images_drawn = False
    for fname, is_img, url in saved_files:
        if is_img:
            images_drawn = True
            img_path = os.path.join(app.config["UPLOAD_FOLDER"], fname)
            try:
                with Image.open(img_path) as im:
                    max_w, max_h = width - 2 * margin, 3.5 * inch
                    im_w, im_h = im.size
                    ratio = min(max_w / im_w, max_h / im_h, 1)
                    draw_w, draw_h = im_w * ratio, im_h * ratio
                    if y - draw_h < margin + 80:
                        c.showPage()
                        add_logo(c)
                        y = height - margin - 80
                    c.drawImage(img_path, margin, y - draw_h, width=draw_w, height=draw_h, preserveAspectRatio=True, mask='auto')
                    y -= draw_h + 12
            except Exception as e:
                c.setFont("Helvetica-Oblique", 9)
                c.drawString(margin, y, f"(Error embedding {fname}: {e})")
                y -= 12

    # --- Add non-image attachments (videos, logs, etc.) ---
    non_images = [(f, u) for f, is_img, u in saved_files if not is_img]
    if non_images:
        if y < margin + 80:
            c.showPage()
            add_logo(c)
            y = height - margin - 80
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margin, y, "Attachments:")
        y -= 14
        c.setFont("Helvetica", 9)
        for fname, url in non_images:
            text_lines = [f"- {fname}", url]
            for ln in text_lines:
                c.drawString(margin, y, ln)
                y -= 12
            y -= 6

    # --- GitHub issue link ---
    if y < margin + 60:
        c.showPage()
        add_logo(c)
        y = height - margin - 80
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "GitHub Issue Link:")
    c.setFont("Helvetica", 9)
    c.drawString(margin, y - 14, str(issue_link))
    y -= 40

    c.showPage()
    c.save()
    pdf_buffer.seek(0)

    filename_out = f"{secure_filename(site_name)}_{date_val}_RCA.pdf"
    return send_file(pdf_buffer, as_attachment=True, download_name=filename_out, mimetype="application/pdf")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
