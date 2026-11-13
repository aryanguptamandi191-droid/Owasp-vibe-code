import os
import sqlite3
from datetime import date, datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from config import Config
from models import db, Application, STATUS_CHOICES, STATUS_COLORS

def migrate_db_columns(app):
    """Ensure all new columns exist in SQLite database if it was already created."""
    with app.app_context():
        db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(applications)")
            columns = [info[1] for info in cursor.fetchall()]

            new_columns = [
                ('is_dream_company', 'BOOLEAN NOT NULL DEFAULT 0'),
                ('is_archived', 'BOOLEAN NOT NULL DEFAULT 0'),
                ('archived_at', 'DATETIME'),
                ('rejection_date', 'DATE'),
                ('stipend', 'VARCHAR(100)'),
                ('location', 'VARCHAR(150)'),
                ('perks', 'TEXT'),
                ('offer_deadline', 'DATE'),
            ]

            for col_name, col_type in new_columns:
                if col_name not in columns:
                    try:
                        cursor.execute(f"ALTER TABLE applications ADD COLUMN {col_name} {col_type}")
                    except Exception as e:
                        print(f"Migration notice for {col_name}: {e}")

            conn.commit()
            conn.close()

def run_automated_maintenance():
    """
    Automated tasks:
    1. Auto-archive applications without any updates for > 15 days.
    2. Auto-delete rejected applications 2 days after rejection.
    """
    today = date.today()
    fifteen_days_ago = today - timedelta(days=15)
    two_days_ago = today - timedelta(days=2)

    # 1. Auto-archive inactive applications (>15 days without update, not already archived, not Offer)
    inactive_apps = Application.query.filter(
        Application.is_archived.is_(False),
        Application.status != 'Offer',
        Application.updated_at < datetime.combine(fifteen_days_ago, datetime.min.time())
    ).all()

    archived_count = 0
    for app_item in inactive_apps:
        app_item.is_archived = True
        app_item.archived_at = datetime.utcnow()
        archived_count += 1

    # 2. Auto-delete rejected applications after 2 days
    # If rejection_date is None but status is Rejected, set rejection_date to today
    unmarked_rejected = Application.query.filter(
        Application.status == 'Rejected',
        Application.rejection_date.is_(None)
    ).all()
    for item in unmarked_rejected:
        # Use updated_at or today
        item.rejection_date = item.updated_at.date() if item.updated_at else today

    # Now permanently delete rejected applications older than 2 days
    expired_rejected = Application.query.filter(
        Application.status == 'Rejected',
        Application.rejection_date <= two_days_ago
    ).all()

    deleted_count = 0
    for item in expired_rejected:
        db.session.delete(item)
        deleted_count += 1

    if archived_count > 0 or deleted_count > 0:
        db.session.commit()

    return {'archived': archived_count, 'deleted': deleted_count}

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)

    with app.app_context():
        db.create_all()
        # Run SQLite migration if needed
        if not app.config.get('TESTING'):
            migrate_db_columns(app)

    @app.before_request
    def before_request_maintenance():
        # Only run on standard page requests to keep DB fresh
        if not request.path.startswith('/static') and not app.config.get('TESTING'):
            try:
                run_automated_maintenance()
            except Exception as e:
                print(f"Maintenance check: {e}")

    # Context processor
    @app.context_processor
    def inject_globals():
        return {
            'today': date.today(),
            'status_choices': STATUS_CHOICES,
            'status_colors': STATUS_COLORS,
            'active_offers_count': Application.query.filter_by(status='Offer', is_archived=False).count(),
            'dream_companies_count': Application.query.filter_by(is_dream_company=True, is_archived=False).count(),
            'archived_count': Application.query.filter_by(is_archived=True).count()
        }

    @app.route('/')
    def index():
        return redirect(url_for('dashboard'))

    @app.route('/dashboard')
    def dashboard():
        # Exclude archived applications from active pipeline statistics
        active_apps_query = Application.query.filter_by(is_archived=False)

        total_count = active_apps_query.count()
        applied_count = active_apps_query.filter_by(status='Applied').count()
        test_count = active_apps_query.filter_by(status='Test').count()
        interview_count = active_apps_query.filter_by(status='Interview').count()
        offer_count = active_apps_query.filter_by(status='Offer').count()
        rejected_count = active_apps_query.filter_by(status='Rejected').count()
        dream_count = active_apps_query.filter_by(is_dream_company=True).count()
        archived_total = Application.query.filter_by(is_archived=True).count()

        stats = {
            'total': total_count,
            'applied': applied_count,
            'test': test_count,
            'interview': interview_count,
            'offer': offer_count,
            'rejected': rejected_count,
            'dream': dream_count,
            'archived': archived_total,
            'applied_pct': round((applied_count / total_count * 100), 1) if total_count else 0,
            'test_pct': round((test_count / total_count * 100), 1) if total_count else 0,
            'interview_pct': round((interview_count / total_count * 100), 1) if total_count else 0,
            'offer_pct': round((offer_count / total_count * 100), 1) if total_count else 0,
            'rejected_pct': round((rejected_count / total_count * 100), 1) if total_count else 0,
        }

        # Upcoming deadlines: only active applications with deadline_date, sorted nearest first
        deadlines_query = active_apps_query.filter(Application.deadline_date.isnot(None))\
            .order_by(Application.deadline_date.asc())
        upcoming_deadlines = deadlines_query.limit(5).all()
        total_deadlines_count = deadlines_query.count()

        today = date.today()
        three_days_later = today + timedelta(days=3)
        overdue_count = active_apps_query.filter(
            Application.deadline_date.isnot(None),
            Application.deadline_date < today
        ).count()
        urgent_count = active_apps_query.filter(
            Application.deadline_date.isnot(None),
            Application.deadline_date >= today,
            Application.deadline_date <= three_days_later
        ).count()

        # Recent applications for dashboard table
        q = request.args.get('q', '').strip()
        status_filter = request.args.get('status', 'all').strip()

        recent_query = active_apps_query
        if q:
            search = f"%{q}%"
            recent_query = recent_query.filter(
                (Application.company_name.ilike(search)) |
                (Application.job_role.ilike(search)) |
                (Application.notes.ilike(search))
            )
        if status_filter and status_filter != 'all':
            recent_query = recent_query.filter(Application.status == status_filter)

        recent_applications = recent_query.order_by(Application.application_date.desc(), Application.id.desc()).limit(8).all()

        return render_template(
            'dashboard.html',
            stats=stats,
            upcoming_deadlines=upcoming_deadlines,
            total_deadlines_count=total_deadlines_count,
            overdue_count=overdue_count,
            urgent_count=urgent_count,
            recent_applications=recent_applications,
            q=q,
            status_filter=status_filter,
            page_title='Dashboard'
        )

    @app.route('/applications')
    def applications():
        q = request.args.get('q', '').strip()
        status_filter = request.args.get('status', 'all').strip()
        sort_by = request.args.get('sort', 'app_date_desc').strip()
        view_mode = request.args.get('view', 'active').strip() # 'active' or 'archived'
        dream_only = request.args.get('dream', '').strip()

        query = Application.query

        if view_mode == 'archived':
            query = query.filter_by(is_archived=True)
        else:
            query = query.filter_by(is_archived=False)

        if dream_only == '1':
            query = query.filter_by(is_dream_company=True)

        if q:
            search = f"%{q}%"
            query = query.filter(
                (Application.company_name.ilike(search)) |
                (Application.job_role.ilike(search)) |
                (Application.notes.ilike(search))
            )

        if status_filter and status_filter != 'all':
            query = query.filter(Application.status == status_filter)

        if sort_by == 'app_date_asc':
            query = query.order_by(Application.application_date.asc())
        elif sort_by == 'company_asc':
            query = query.order_by(Application.company_name.asc())
        elif sort_by == 'deadline_asc':
            query = query.order_by(Application.deadline_date.is_(None), Application.deadline_date.asc())
        else:
            query = query.order_by(Application.application_date.desc(), Application.id.desc())

        all_applications = query.all()

        # Dynamic pill counts for active items
        active_base = Application.query.filter_by(is_archived=False)
        pill_counts = {
            'all': active_base.count(),
            'dream': active_base.filter_by(is_dream_company=True).count(),
            'Applied': active_base.filter_by(status='Applied').count(),
            'Test': active_base.filter_by(status='Test').count(),
            'Interview': active_base.filter_by(status='Interview').count(),
            'Offer': active_base.filter_by(status='Offer').count(),
            'Rejected': active_base.filter_by(status='Rejected').count(),
            'archived': Application.query.filter_by(is_archived=True).count()
        }

        return render_template(
            'applications.html',
            applications=all_applications,
            q=q,
            status_filter=status_filter,
            sort_by=sort_by,
            view_mode=view_mode,
            dream_only=dream_only,
            pill_counts=pill_counts,
            page_title='All Applications' if view_mode != 'archived' else 'Archived Applications'
        )

    @app.route('/applications/new', methods=['GET', 'POST'])
    def add_application():
        if request.method == 'POST':
            company_name = request.form.get('company_name', '').strip()
            job_role = request.form.get('job_role', '').strip()
            job_description = request.form.get('job_description', '').strip()
            company_website = request.form.get('company_website', '').strip()
            application_date_str = request.form.get('application_date', '').strip()
            status = request.form.get('status', 'Applied').strip()
            deadline_date_str = request.form.get('deadline_date', '').strip()
            interview_date_str = request.form.get('interview_date', '').strip()
            notes = request.form.get('notes', '').strip()
            is_dream_company = bool(request.form.get('is_dream_company'))

            # Offer details
            stipend = request.form.get('stipend', '').strip()
            location = request.form.get('location', '').strip()
            perks = request.form.get('perks', '').strip()
            offer_deadline_str = request.form.get('offer_deadline', '').strip()

            errors = []
            if not company_name:
                errors.append('Company name is required.')
            if not job_role:
                errors.append('Job role / position is required.')
            if not application_date_str:
                errors.append('Application date is required.')
            if status not in STATUS_CHOICES:
                errors.append(f'Invalid status selected. Must be one of: {", ".join(STATUS_CHOICES)}.')

            app_date = None
            deadline_date = None
            interview_date = None
            offer_deadline = None

            if application_date_str:
                try:
                    app_date = datetime.strptime(application_date_str, '%Y-%m-%d').date()
                except ValueError:
                    errors.append('Invalid application date format. Use YYYY-MM-DD.')

            if deadline_date_str:
                try:
                    deadline_date = datetime.strptime(deadline_date_str, '%Y-%m-%d').date()
                except ValueError:
                    errors.append('Invalid deadline date format. Use YYYY-MM-DD.')

            if interview_date_str:
                try:
                    interview_date = datetime.strptime(interview_date_str, '%Y-%m-%d').date()
                except ValueError:
                    errors.append('Invalid interview date format. Use YYYY-MM-DD.')

            if offer_deadline_str:
                try:
                    offer_deadline = datetime.strptime(offer_deadline_str, '%Y-%m-%d').date()
                except ValueError:
                    errors.append('Invalid offer deadline format. Use YYYY-MM-DD.')

            if company_website and not company_website.startswith(('http://', 'https://')):
                company_website = 'https://' + company_website

            if errors:
                for error in errors:
                    flash(error, 'danger')
                return render_template('add_application.html', form_data=request.form, page_title='Add Application')

            # Rejection date if marked rejected immediately
            rejection_date = date.today() if status == 'Rejected' else None

            app_record = Application(
                company_name=company_name,
                job_role=job_role,
                job_description=job_description or None,
                company_website=company_website or None,
                application_date=app_date,
                status=status,
                deadline_date=deadline_date,
                interview_date=interview_date,
                notes=notes or None,
                is_dream_company=is_dream_company,
                rejection_date=rejection_date,
                stipend=stipend or None,
                location=location or None,
                perks=perks or None,
                offer_deadline=offer_deadline
            )

            db.session.add(app_record)
            db.session.commit()

            flash(f'Application for {company_name} ({job_role}) added successfully!', 'success')
            return redirect(url_for('applications'))

        default_data = {
            'application_date': date.today().strftime('%Y-%m-%d'),
            'status': 'Applied'
        }
        return render_template('add_application.html', form_data=default_data, page_title='Add Application')

    @app.route('/applications/<int:app_id>/edit', methods=['GET', 'POST'])
    def edit_application(app_id):
        app_record = db.get_or_404(Application, app_id)

        if request.method == 'POST':
            company_name = request.form.get('company_name', '').strip()
            job_role = request.form.get('job_role', '').strip()
            job_description = request.form.get('job_description', '').strip()
            company_website = request.form.get('company_website', '').strip()
            application_date_str = request.form.get('application_date', '').strip()
            status = request.form.get('status', '').strip()
            deadline_date_str = request.form.get('deadline_date', '').strip()
            interview_date_str = request.form.get('interview_date', '').strip()
            notes = request.form.get('notes', '').strip()
            is_dream_company = bool(request.form.get('is_dream_company'))

            stipend = request.form.get('stipend', '').strip()
            location = request.form.get('location', '').strip()
            perks = request.form.get('perks', '').strip()
            offer_deadline_str = request.form.get('offer_deadline', '').strip()

            errors = []
            if not company_name:
                errors.append('Company name is required.')
            if not job_role:
                errors.append('Job role / position is required.')
            if not application_date_str:
                errors.append('Application date is required.')
            if status not in STATUS_CHOICES:
                errors.append(f'Invalid status selected. Must be one of: {", ".join(STATUS_CHOICES)}.')

            app_date, deadline_date, interview_date, offer_deadline = None, None, None, None

            if application_date_str:
                try:
                    app_date = datetime.strptime(application_date_str, '%Y-%m-%d').date()
                except ValueError:
                    errors.append('Invalid application date format.')

            if deadline_date_str:
                try:
                    deadline_date = datetime.strptime(deadline_date_str, '%Y-%m-%d').date()
                except ValueError:
                    errors.append('Invalid deadline date format.')

            if interview_date_str:
                try:
                    interview_date = datetime.strptime(interview_date_str, '%Y-%m-%d').date()
                except ValueError:
                    errors.append('Invalid interview date format.')

            if offer_deadline_str:
                try:
                    offer_deadline = datetime.strptime(offer_deadline_str, '%Y-%m-%d').date()
                except ValueError:
                    errors.append('Invalid offer deadline format.')

            if company_website and not company_website.startswith(('http://', 'https://')):
                company_website = 'https://' + company_website

            if errors:
                for error in errors:
                    flash(error, 'danger')
                return render_template(
                    'edit_application.html',
                    app_record=app_record,
                    form_data=request.form,
                    page_title=f'Edit {app_record.company_name}'
                )

            # Check rejection date if newly rejected
            if status == 'Rejected' and app_record.status != 'Rejected':
                app_record.rejection_date = date.today()
            elif status != 'Rejected':
                app_record.rejection_date = None

            app_record.company_name = company_name
            app_record.job_role = job_role
            app_record.job_description = job_description or None
            app_record.company_website = company_website or None
            app_record.application_date = app_date
            app_record.status = status
            app_record.deadline_date = deadline_date
            app_record.interview_date = interview_date
            app_record.notes = notes or None
            app_record.is_dream_company = is_dream_company
            app_record.stipend = stipend or None
            app_record.location = location or None
            app_record.perks = perks or None
            app_record.offer_deadline = offer_deadline
            app_record.updated_at = datetime.utcnow()

            # If user edits an archived application, unarchive it automatically
            if app_record.is_archived:
                app_record.is_archived = False
                app_record.archived_at = None

            db.session.commit()
            flash(f'Application for {company_name} updated successfully!', 'success')
            return redirect(url_for('applications'))

        return render_template('edit_application.html', app_record=app_record, page_title=f'Edit {app_record.company_name}')

    @app.route('/applications/<int:app_id>/delete', methods=['POST'])
    def delete_application(app_id):
        app_record = db.get_or_404(Application, app_id)
        company = app_record.company_name
        role = app_record.job_role
        db.session.delete(app_record)
        db.session.commit()
        flash(f'Application for {company} ({role}) was permanently deleted.', 'info')

        next_url = request.form.get('next') or request.referrer or url_for('applications')
        return redirect(next_url)

    @app.route('/api/applications/<int:app_id>/status', methods=['POST'])
    def update_status(app_id):
        app_record = db.get_or_404(Application, app_id)
        new_status = request.form.get('status') or (request.json.get('status') if request.is_json else None)

        if not new_status or new_status not in STATUS_CHOICES:
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'error': 'Invalid status'}), 400
            flash('Invalid status provided.', 'danger')
            return redirect(request.referrer or url_for('applications'))

        # Track rejection date for auto-delete
        if new_status == 'Rejected' and app_record.status != 'Rejected':
            app_record.rejection_date = date.today()
        elif new_status != 'Rejected':
            app_record.rejection_date = None

        app_record.status = new_status
        app_record.updated_at = datetime.utcnow()
        db.session.commit()

        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': True,
                'id': app_record.id,
                'status': app_record.status,
                'status_config': app_record.status_config,
                'rejection_date': app_record.rejection_date.strftime('%Y-%m-%d') if app_record.rejection_date else None,
                'message': f'Status updated to {new_status}'
            })

        flash(f'Status updated to {new_status} for {app_record.company_name}.', 'success')
        return redirect(request.referrer or url_for('applications'))

    @app.route('/api/applications/<int:app_id>/dream', methods=['POST'])
    def toggle_dream_company(app_id):
        """Toggle dream company star status via AJAX."""
        app_record = db.get_or_404(Application, app_id)
        app_record.is_dream_company = not app_record.is_dream_company
        app_record.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify({
            'success': True,
            'id': app_record.id,
            'is_dream_company': app_record.is_dream_company,
            'message': f'{"Marked as" if app_record.is_dream_company else "Removed from"} Dream Company'
        })

    @app.route('/applications/<int:app_id>/unarchive', methods=['POST'])
    def unarchive_application(app_id):
        """Restore an archived application back to active pipeline."""
        app_record = db.get_or_404(Application, app_id)
        app_record.is_archived = False
        app_record.archived_at = None
        app_record.updated_at = datetime.utcnow()
        db.session.commit()
        flash(f'{app_record.company_name} has been restored to your active pipeline.', 'success')
        return redirect(request.referrer or url_for('applications'))

    @app.route('/offers/compare')
    def offer_comparison():
        """Dedicated Offer Comparison page for evaluating final offers side-by-side."""
        offers = Application.query.filter_by(status='Offer', is_archived=False).all()
        return render_template(
            'offer_comparison.html',
            offers=offers,
            page_title='Offer Comparison'
        )

    @app.route('/deadlines')
    def deadlines():
        filter_mode = request.args.get('filter', 'all').strip()

        # Active applications with deadline_date only!
        query = Application.query.filter(
            Application.deadline_date.isnot(None),
            Application.is_archived.is_(False)
        )

        today = date.today()
        three_days_later = today + timedelta(days=3)

        if filter_mode == 'urgent':
            query = query.filter(Application.deadline_date >= today, Application.deadline_date <= three_days_later)
        elif filter_mode == 'overdue':
            query = query.filter(Application.deadline_date < today)
        elif filter_mode == 'upcoming':
            query = query.filter(Application.deadline_date >= today)

        deadlines_list = query.order_by(Application.deadline_date.asc()).all()

        total_with_deadlines = Application.query.filter(
            Application.deadline_date.isnot(None),
            Application.is_archived.is_(False)
        ).count()
        overdue_total = Application.query.filter(
            Application.deadline_date.isnot(None),
            Application.deadline_date < today,
            Application.is_archived.is_(False)
        ).count()
        urgent_total = Application.query.filter(
            Application.deadline_date.isnot(None),
            Application.deadline_date >= today,
            Application.deadline_date <= three_days_later,
            Application.is_archived.is_(False)
        ).count()

        return render_template(
            'deadlines.html',
            deadlines=deadlines_list,
            filter_mode=filter_mode,
            total_with_deadlines=total_with_deadlines,
            overdue_total=overdue_total,
            urgent_total=urgent_total,
            page_title='Upcoming Deadlines'
        )

    @app.route('/api/stats')
    def api_stats():
        active = Application.query.filter_by(is_archived=False)
        total = active.count()
        counts = {s: active.filter_by(status=s).count() for s in STATUS_CHOICES}
        return jsonify({
            'total': total,
            'counts': counts,
            'dream': active.filter_by(is_dream_company=True).count(),
            'archived': Application.query.filter_by(is_archived=True).count()
        })

    @app.route('/api/applications/<int:app_id>')
    def api_application_detail(app_id):
        app_record = db.get_or_404(Application, app_id)
        return jsonify(app_record.to_dict())

    @app.route('/seed-sample-data', methods=['POST'])
    def seed_data():
        today = date.today()

        sample_apps = [
            # 1. Accepted Offer with full compensation details
            Application(
                company_name='Stripe',
                job_role='Software Engineer Intern - Payments Core',
                job_description='Building high-reliability financial infrastructure and APIs for global commerce.',
                company_website='https://stripe.com/jobs',
                application_date=today - timedelta(days=22),
                status='Offer',
                deadline_date=today + timedelta(days=5),
                is_dream_company=True,
                stipend='$58 / hour + $3,000 relocation',
                location='San Francisco, CA (Hybrid)',
                perks='Housing stipend, 1:1 mentorship with staff engineer, full health insurance, catered gourmet meals, fast-track return offer',
                offer_deadline=today + timedelta(days=5),
                notes='Official written offer letter received! Tech stack: Ruby, Go, React, Distributed Systems.'
            ),
            # 2. Another Accepted Offer for side-by-side comparison
            Application(
                company_name='Datadog',
                job_role='Distributed Systems Intern - Cloud Observability',
                job_description='High-throughput telemetry ingestion pipelines, Kafka clustering, and Go agents.',
                company_website='https://www.datadoghq.com/careers',
                application_date=today - timedelta(days=28),
                status='Offer',
                deadline_date=today + timedelta(days=8),
                is_dream_company=True,
                stipend='$52 / hour + $2,500 housing stipend',
                location='New York, NY (In-Office)',
                perks='Downtown NYC office overlooking Hudson, transit subsidy, hackathon stipend, return offer rate > 80%',
                offer_deadline=today + timedelta(days=8),
                notes='Team match confirmed with Core Ingestion infrastructure group.'
            ),
            # 3. Dream Company with Urgent Deadline (2 days left - triggers RED ALERT symbol!)
            Application(
                company_name='Google',
                job_role='STEP Intern (Software Engineering)',
                job_description='12-week development internship program working on production infrastructure and tooling.',
                company_website='https://careers.google.com',
                application_date=today - timedelta(days=10),
                status='Test',
                deadline_date=today + timedelta(days=2), # <= 3 days -> triggers RED SYMBOL!
                is_dream_company=True,
                notes='Online Assessment link received. 48-hour submission window active on HackerRank!'
            ),
            # 4. Another Application with 1 day deadline (triggers RED ALERT symbol!)
            Application(
                company_name='Notion',
                job_role='Product Engineering Intern',
                job_description='Crafting delightful user experiences across documents, databases, and AI workspace agents.',
                company_website='https://www.notion.so/careers',
                application_date=today - timedelta(days=8),
                status='Interview',
                deadline_date=today + timedelta(days=1), # <= 3 days -> triggers RED SYMBOL!
                is_dream_company=True,
                interview_date=today + timedelta(days=1),
                notes='Technical design interview with engineering manager.'
            ),
            # 5. Application with future deadline (> 3 days, normal display)
            Application(
                company_name='Figma',
                job_role='Frontend Engineer Intern',
                job_description='Working on real-time multiplayer canvas rendering and collaboration tools using WebGL & TypeScript.',
                company_website='https://www.figma.com/careers',
                application_date=today - timedelta(days=4),
                status='Applied',
                deadline_date=today + timedelta(days=12),
                is_dream_company=False,
                notes='Submitted portfolio detailing WebGL experiments.'
            ),
            # 6. Recently rejected application (within 2 days - shows auto-delete countdown!)
            Application(
                company_name='Spotify',
                job_role='Backend Engineering Intern',
                job_description='Music recommendation features and high-throughput streaming systems.',
                company_website='https://www.lifeatspotify.com',
                application_date=today - timedelta(days=14),
                status='Rejected',
                rejection_date=today - timedelta(days=1), # 1 day ago -> auto-deletes tomorrow!
                is_dream_company=False,
                notes='Polite rejection after initial screen. System will auto-delete this tomorrow.'
            ),
            # 7. Inactive application (> 15 days ago without update - showcase auto-archive)
            Application(
                company_name='Uber',
                job_role='Data Science Intern',
                job_description='Marketplace pricing algorithms and demand modeling.',
                company_website='https://uber.com/careers',
                application_date=today - timedelta(days=25),
                status='Applied',
                is_dream_company=False,
                is_archived=True,
                archived_at=datetime.utcnow() - timedelta(days=2),
                notes='No response from company after 20 days. Automatically moved to archive.'
            )
        ]

        # Reset and load fresh samples
        Application.query.delete()
        for app_obj in sample_apps:
            db.session.add(app_obj)
        db.session.commit()

        flash('Sample applications loaded! Dream companies, red-alert deadlines, offer comparisons, and auto-archive are active.', 'success')
        return redirect(url_for('dashboard'))

    @app.route('/reset-data', methods=['POST'])
    def reset_data():
        Application.query.delete()
        db.session.commit()
        flash('All applications cleared. You have a fresh, clean database.', 'info')
        return redirect(url_for('dashboard'))

    return app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    url = f"http://127.0.0.1:{port}"
    print(f"🚀 InternTrack upgraded running on {url}")

    # Automatically open local site in browser on startup
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        import webbrowser
        import threading
        import time

        def open_browser():
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=open_browser, daemon=True).start()

    app.run(host='127.0.0.1', port=port, debug=True)
