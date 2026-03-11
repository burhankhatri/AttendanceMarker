from flask import Blueprint, render_template, request, redirect, url_for, flash
from database.db import db
from database.models import Settings

settings_bp = Blueprint('settings', __name__)


@settings_bp.route('/setup', methods=['GET', 'POST'])
def setup():
    settings = Settings.get()
    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        roll_number = request.form.get('roll_number', '').strip()
        variants_raw = request.form.get('name_variants', '').strip()

        if not display_name:
            flash('Name is required', 'error')
            return render_template('setup.html', settings=settings)

        settings.display_name = display_name
        settings.roll_number = roll_number or None
        settings.name_variants = [v.strip() for v in variants_raw.split(',') if v.strip()] if variants_raw else []
        db.session.commit()

        flash('Settings saved!', 'success')
        return redirect(url_for('dashboard.dashboard'))

    return render_template('setup.html', settings=settings)
