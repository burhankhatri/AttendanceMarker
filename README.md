# MeetBot

<p align="center">
  A simple local app to help you manage attendance-style workflows for Google Meet and Microsoft Teams.
</p>

---

## What This App Does

MeetBot gives you one place to:

- save your attendance identity (name, roll number, name variants),
- record your "present" audio once,
- connect your real browser profile,
- join a Meet or Teams link from the app,
- track live session status from a clean dashboard.

---

## What Changed Recently

- Microsoft Edge support was improved.
- Google Chrome support remains available.
- Microsoft Teams flow is now included alongside Google Meet.
- Browser setup became easier with profile detection.
- Meeting status page now gives clearer live updates.

---

## Quick Start

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Start the app

```bat
start.bat
```

If needed, you can also run:

```bash
python app.py
```

### 3. Open dashboard

Go to:

`http://127.0.0.1:5001/dashboard`

---

## First-Time Setup (In App)

Follow these pages in order:

1. **Dashboard**
2. **Settings**
   - Enter your display name.
   - Add roll number and name variants if you want better detection.
3. **Record Audio**
   - Record your "present" response.
4. **Browser Setup**
   - Choose **Edge** or **Chrome**.
   - Select the profile you actually use for Meet/Teams.
   - Click launch, confirm you are signed in, then close that browser window.
5. **Join Meeting**
   - Paste a Meet or Teams link and start.

---

## Daily Use

1. Open **Join Meeting**.
2. Paste your meeting link.
3. Start the session.
4. Watch **Meeting Status** for live progress.
5. Click **Leave Meeting** when done.

---

## Known Issues

- Browser profile access can fail if the same profile is busy.
- If your login session expires, run Browser Setup again.
- Meet/Teams interface updates can occasionally affect join flow.
- Some sessions may need a retry if the platform is slow to load.

---

### PLEASE NOTE:

For legal reasons,  
This bot was purely made for ***educational*** purposes only and is meant as a fun way to learn and implement the libraries/packages mentioned above.   
This bot is not meant to be used in any malicious way and we are not responsible for anyone actually using this bot to wrongfully attend online classes on his/her/their behalf.
