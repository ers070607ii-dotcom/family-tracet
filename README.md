# Family Budget Tracker

Stack: HTML + CSS + JavaScript (frontend), Python Flask (backend), SQL (SQLite database).

## Run
    python -m venv venv
    venv\Scripts\activate        (Windows)   |   source venv/bin/activate   (Mac/Linux)
    pip install -r requirements.txt
    python app.py

Open http://127.0.0.1:5000

## Structure
    app.py              Python backend (API, validation, duplicate check, 90% alert)
    schema.sql          SQL tables and constraints
    templates/          index.html (languages, register, login), scan.html (scan and save receipt)
    static/css/         style.css
    static/js/          common.js (translations, API helper), index.js, scan.js
    uploads/            saved receipt photos (created automatically)
    budget.db           SQLite database (created automatically)

## Roles
    Household admin: registers first, gets a household code, can delete records and change limits.
    Family member: registers with the household code, can scan and save receipts.
