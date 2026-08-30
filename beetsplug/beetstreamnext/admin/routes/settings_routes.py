from typing import Any
import flask

from .. import admin_bp, admin_required, back_to

from beetsplug.beetstreamnext.utils.general import get_server_info, human_bytes, external_url
from beetsplug.beetstreamnext.core.logging import bsn_logger, mem_log
from beetsplug.beetstreamnext.core.maintenance import cache_disk_usage
from beetsplug.beetstreamnext.core.health import flagged_songs
from beetsplug.beetstreamnext.core.users_crud import load_all_users
from beetsplug.beetstreamnext.core.tempstore import temporary_store
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.external import test_lastfm_connection, test_audiomuse_connection
from beetsplug.beetstreamnext.schemas import SETTINGS_SCHEMA, SETTINGS_CATEGORIES, PUBLIC_USER_FIELDS, USER_ROLES_SCHEMA
from beetsplug.beetstreamnext.admin.forms import UserForm, EditUserForm, RadioStationForm
from beetsplug.beetstreamnext.settings import settings_store


_CAN_MULTISELECT = {'external_playlists_editors'}
_CHAT_PAGE_SIZE = 50


##
# Settings-updating routes

@admin_bp.route('/settings/<category>', methods=['POST'])
@admin_required
def route_update_settings(category: str) -> flask.Response:

    if category not in SETTINGS_CATEGORIES:
        flask.abort(404)

    submitted = flask.request.form
    errors: list[str] = []
    updated: list[str] = []
    restart_needed = False

    for key, spec in SETTINGS_SCHEMA.items():
        if spec.get('category') != category:
            continue
        if spec['type'] == 'list[str]' and key not in _CAN_MULTISELECT:
            continue   # Handled by dedicated endpoints

        if spec['type'] == 'bool':
            value: Any = key in submitted
        elif key in _CAN_MULTISELECT:
            value = submitted.getlist(key)
        elif key in submitted:
            value = submitted[key]
            if spec.get('sensitive') and value == '':
                continue   # leave unchanged
        else:
            continue

        try:
            current = settings_store.get(key)
            new_value = settings_store.set(key, value)
            if new_value != current:
                updated.append(key)
                if spec.get('requires_restart'):
                    restart_needed = True
        except (ValueError, TypeError) as e:
            errors.append(f"{key}: {e}")
            bsn_logger.warning(f"Invalid value submitted for '{key}': {e}")
        except Exception as e:
            # .set() re-raises applicable failures after persisting
            errors.append(f'{key}: saved, but failed to apply: {e}')
            bsn_logger.error(f"Live-apply failed for '{key}': {e}")

    for err in errors:
        flask.flash(err, 'error')

    if updated and not errors:
        plur = 's' if len(updated) > 1 else ''
        msg = f'Updated {len(updated)} setting{plur}.'
        if restart_needed:
            msg += ' Some changes require a server restart to take effect.'
        flask.flash(msg, 'info' if restart_needed else 'success')
    elif not updated and not errors:
        flask.flash('No changes.', 'info')

    return back_to(category)


##
# Sensitive settings: dedicated clearing endpoint

@admin_bp.route('/settings/<category>/clear/<key>', methods=['POST'])
@admin_required
def route_clear_setting(category: str, key: str) -> flask.Response:
    if category not in SETTINGS_CATEGORIES:
        flask.abort(404)
    spec = SETTINGS_SCHEMA.get(key)
    if not spec or spec.get('category') != category or not spec.get('sensitive'):
        flask.abort(404)

    settings_store.set(key, '')
    flask.flash(f"Cleared '{key}'.", 'success')
    return back_to(category)


##
# Library integrations: test connection

@admin_bp.route('/settings/test/lastfm', methods=['GET'])
@admin_required
def route_test_lastfm() -> flask.Response:
    ok, message = test_lastfm_connection()
    return flask.jsonify({'ok': ok, 'message': message})


@admin_bp.route('/settings/test/audiomuse', methods=['GET'])
@admin_required
def route_test_audiomuse() -> flask.Response:
    ok, message = test_audiomuse_connection()
    return flask.jsonify({'ok': ok, 'message': message})


@admin_bp.route('/')
@admin_required
def route_settings() -> flask.Response:
    token = flask.session.pop('_api_key_token', None)
    new_api_key = temporary_store.claim(token)

    settings_by_category = {cat: settings_store.get_for_ui(cat) for cat in SETTINGS_CATEGORIES}
    host_suggestions = flask.current_app.config.get('HOST_LIST', [])

    cache_size = human_bytes(cache_disk_usage(
        flask.current_app.config['THUMBNAIL_CACHE_PATH'],
        flask.current_app.config['HTTP_CACHE_PATH']
    ))

    users = load_all_users(fields=list(PUBLIC_USER_FIELDS) + ['avatarLastChanged'])
    for u in users:
        u['hasAvatar'] = bool(u.get('avatarLastChanged'))

    # Load chat messages for moderation
    chat_page = max(1, flask.request.args.get('chat_page', default=1, type=int))
    with database() as db:
        chat_total = db.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        chat_pages = max(1, -(-chat_total // _CHAT_PAGE_SIZE))
        chat_page = min(chat_page, chat_pages)
        chat_messages = db.execute(
            """
            SELECT id, username, time, message
            FROM chat_messages
            ORDER BY time DESC
            LIMIT ? OFFSET ?
            """, (_CHAT_PAGE_SIZE, (chat_page - 1) * _CHAT_PAGE_SIZE)
        ).fetchall()

    # Load active shares
    with database() as db:
        shares_rows = db.execute(
            """
            SELECT s.id, s.username, s.description, s.expires, s.created, s.visit_count,
                   (SELECT COUNT(*) FROM share_entries se WHERE se.share_id = s.id) as entry_count
            FROM shares s
            ORDER BY s.created DESC
            """
        ).fetchall()

    # build shares URLs
    shares_list = []

    for r in shares_rows:
        s_dict = dict(r)
        s_dict['url'] = external_url(flask.url_for('public.share_view', share_id=r['id']))
        shares_list.append(s_dict)

    # Load radio stations
    with database() as db:
        radio_rows = db.execute(
            """
            SELECT id, name, stream_url, homepage_url, (image IS NOT NULL) AS has_image
            FROM internet_radio_stations
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()

    radios = [dict(r) for r in radio_rows]

    # Load podcast channels, subscribers, episode count and disk usage
    with database() as db:
        channel_rows = db.execute(
            """
            SELECT pc.id, pc.title, pc.url, pc.status, pc.error_message,
                   (pc.image IS NOT NULL) AS has_image,
                   (SELECT COUNT(*) FROM podcast_episodes pe WHERE pe.channel_id = pc.id) AS episode_count,
                   (SELECT COUNT(*) FROM podcast_episodes pe
                    WHERE pe.channel_id = pc.id AND pe.status = 'completed') AS downloaded_count,
                   (SELECT COALESCE(SUM(pe.file_size), 0) FROM podcast_episodes pe
                    WHERE pe.channel_id = pc.id AND pe.status = 'completed') AS bytes_on_disk
            FROM podcast_channels pc
            ORDER BY pc.title COLLATE NOCASE
            """
        ).fetchall()

        episode_rows = db.execute(
            """
            SELECT id, channel_id, title, publish_date, duration, status, file_size, error_message
            FROM podcast_episodes
            ORDER BY publish_date DESC
            """
        ).fetchall()

        subscription_rows = db.execute(
            """
            SELECT channel_id, username
            FROM podcast_subscriptions
            ORDER BY username COLLATE NOCASE
            """
        ).fetchall()

    episodes_by_channel: dict[int, list] = {}
    for row in episode_rows:
        episodes_by_channel.setdefault(row['channel_id'], []).append(dict(row))

    subscribers_by_channel: dict[int, list] = {}
    for row in subscription_rows:
        subscribers_by_channel.setdefault(row['channel_id'], []).append(row['username'])

    podcast_channels = []
    podcast_total_bytes = 0
    for row in channel_rows:
        ch = dict(row)
        ch['episodes'] = episodes_by_channel.get(ch['id'], [])
        ch['subscribers'] = subscribers_by_channel.get(ch['id'], [])
        ch['storage_size'] = human_bytes(ch['bytes_on_disk'])
        podcast_total_bytes += ch['bytes_on_disk']
        podcast_channels.append(ch)

    resp = flask.make_response(
        flask.render_template(
            'settings.html',
            users=users,
            chat_messages=chat_messages,
            chat_page=chat_page,
            chat_pages=chat_pages,
            cache_size=cache_size,
            shares=shares_list,
            radios=radios,
            podcast_channels=podcast_channels,
            podcast_total_size=human_bytes(podcast_total_bytes),
            flagged_songs=flagged_songs(),
            create_form=UserForm(formdata=None),
            edit_form=EditUserForm(formdata=None),
            radio_form=RadioStationForm(formdata=None),
            role_fields=[(name, label) for name, label, _ in USER_ROLES_SCHEMA],
            server_info=get_server_info(extended=True),
            current_username=flask.session.get('username'),
            new_api_key=new_api_key,
            settings_categories=SETTINGS_CATEGORIES,
            settings_by_category=settings_by_category,
            host_suggestions=host_suggestions,
            log_lines=mem_log.recents,
        )
    )
    return resp