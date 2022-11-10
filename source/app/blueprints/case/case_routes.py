#!/usr/bin/env python3
#
#  IRIS Source Code
#  Copyright (C) 2021 - Airbus CyberSecurity (SAS) - DFIR-IRIS Team
#  ir@cyberactionlab.net - contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

# IMPORTS ------------------------------------------------
import binascii
import marshmallow
from flask import Blueprint
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from flask_login import current_user
from flask_socketio import emit
from flask_socketio import join_room
from flask_wtf import FlaskForm
from sqlalchemy import desc

from app import app
from app import db
from app import socket_io
from app.blueprints.case.case_assets_routes import case_assets_blueprint
from app.blueprints.case.case_graphs_routes import case_graph_blueprint
from app.blueprints.case.case_ioc_routes import case_ioc_blueprint
from app.blueprints.case.case_notes_routes import case_notes_blueprint
from app.blueprints.case.case_rfiles_routes import case_rfiles_blueprint
from app.blueprints.case.case_tasks_routes import case_tasks_blueprint
from app.blueprints.case.case_timeline_routes import case_timeline_blueprint
from app.datamgmt.case.case_db import case_exists
from app.datamgmt.case.case_db import case_get_desc_crc
from app.datamgmt.case.case_db import get_activities_report_template
from app.datamgmt.case.case_db import get_case
from app.datamgmt.case.case_db import get_case_report_template
from app.datamgmt.manage.manage_users_db import get_users_list_restricted_from_case
from app.datamgmt.reporter.report_db import export_case_json
from app.forms import PipelinesCaseForm
from app.iris_engine.module_handler.module_handler import list_available_pipelines
from app.iris_engine.utils.tracker import track_activity
from app.models import CaseStatus
from app.models import UserActivity
from app.models.authorization import CaseAccessLevel
from app.models.authorization import User
from app.schema.marshables import TaskLogSchema
from app.util import ac_api_case_requires
from app.util import ac_case_requires
from app.util import ac_socket_requires
from app.util import response_error
from app.util import response_success

app.register_blueprint(case_timeline_blueprint)
app.register_blueprint(case_notes_blueprint)
app.register_blueprint(case_assets_blueprint)
app.register_blueprint(case_ioc_blueprint)
app.register_blueprint(case_rfiles_blueprint)
app.register_blueprint(case_graph_blueprint)
app.register_blueprint(case_tasks_blueprint)

case_blueprint = Blueprint('case',
                           __name__,
                           template_folder='templates')

event_tags = ["Network", "Server", "ActiveDirectory", "Computer", "Malware", "User Interaction"]


# CONTENT ------------------------------------------------
@case_blueprint.route('/case', methods=['GET'])
@ac_case_requires(CaseAccessLevel.read_only, CaseAccessLevel.full_access)
def case_r(caseid, url_redir):

    if url_redir:
        return redirect(url_for('case.case_r', cid=caseid, redirect=True))

    case = get_case(caseid)
    form = FlaskForm()

    reports = get_case_report_template()
    reports = [row for row in reports]

    reports_act = get_activities_report_template()
    reports_act = [row for row in reports_act]

    if not case:
        return render_template('select_case.html')

    desc_crc32, desc = case_get_desc_crc(caseid)

    return render_template('case.html', case=case, desc=desc, crc=desc_crc32,
                           reports=reports, reports_act=reports_act, form=form)


@case_blueprint.route('/case/exists', methods=['GET'])
@ac_api_case_requires(CaseAccessLevel.read_only, CaseAccessLevel.full_access)
def case_exists_r(caseid):

    if case_exists(caseid):
        return response_success('Case exists')
    else:
        return response_error('Case does not exist', 404)


@case_blueprint.route('/case/pipelines-modal', methods=['GET'])
@ac_case_requires(CaseAccessLevel.full_access)
def case_pipelines_modal(caseid, url_redir):
    if url_redir:
        return redirect(url_for('case.case_r', cid=caseid, redirect=True))

    case = get_case(caseid)

    form = PipelinesCaseForm()

    pl = list_available_pipelines()

    form.pipeline.choices = [("{}-{}".format(ap[0], ap[1]['pipeline_internal_name']),
                                         ap[1]['pipeline_human_name'])for ap in pl]

    # Return default page of case management
    pipeline_args = [("{}-{}".format(ap[0], ap[1]['pipeline_internal_name']),
                      ap[1]['pipeline_human_name'], ap[1]['pipeline_args'])for ap in pl]

    return render_template('modal_case_pipelines.html', case=case, form=form, pipeline_args=pipeline_args)


@socket_io.on('change')
@ac_socket_requires(CaseAccessLevel.full_access)
def socket_summary_onchange(data):
    if not current_user.is_authenticated:
        return

    data['last_change'] = current_user.user
    emit('change', data, to=data['channel'], skip_sid=request.sid)


@socket_io.on('save')
@ac_socket_requires(CaseAccessLevel.full_access)
def socket_summary_onsave(data):
    if not current_user.is_authenticated:
        return

    data['last_saved'] = current_user.user
    emit('save', data, to=data['channel'], skip_sid=request.sid)


@socket_io.on('clear_buffer')
@ac_socket_requires(CaseAccessLevel.full_access)
def socket_summary_onchange(message):
    if not current_user.is_authenticated:
        return

    emit('clear_buffer', message)


@socket_io.on('join')
@ac_socket_requires(CaseAccessLevel.full_access)
def get_message(data):
    if not current_user.is_authenticated:
        return

    room = data['channel']
    join_room(room=room)
    emit('join', {'message': f"{current_user.user} just joined"}, room=room)


@case_blueprint.route('/case/summary/update', methods=['POST'])
@ac_api_case_requires(CaseAccessLevel.full_access)
def desc_fetch(caseid):

    js_data = request.get_json()
    case = get_case(caseid)
    if not case:
        return response_error('Invalid case ID')

    case.description = js_data.get('case_description')
    crc = binascii.crc32(case.description.encode('utf-8'))
    db.session.commit()
    track_activity("updated summary", caseid)

    if not request.cookies.get('session'):
        # API call so we propagate the message to everyone
        data = {
            "case_description": case.description,
            "last_saved": current_user.user
        }
        socket_io.emit('save', data, to=f"case-{caseid}")

    return response_success("Summary updated", data=crc)


@case_blueprint.route('/case/summary/fetch', methods=['GET'])
@ac_api_case_requires(CaseAccessLevel.read_only, CaseAccessLevel.full_access)
def summary_fetch(caseid):
    desc_crc32, desc = case_get_desc_crc(caseid)

    return response_success("Summary fetch", data={'case_description': desc, 'crc32': desc_crc32})


@case_blueprint.route('/case/activities/list', methods=['GET'])
@ac_api_case_requires(CaseAccessLevel.read_only, CaseAccessLevel.full_access)
def activity_fetch(caseid):
    ua = UserActivity.query.with_entities(
        UserActivity.activity_date,
        User.name,
        UserActivity.activity_desc,
        UserActivity.is_from_api
    ).filter(
        UserActivity.case_id == caseid
    ).join(
        UserActivity.user
    ).order_by(
        desc(UserActivity.activity_date)
    ).limit(40).all()

    output = [a._asdict() for a in ua]

    return response_success("", data=output)


@case_blueprint.route("/case/export", methods=['GET'])
@ac_api_case_requires(CaseAccessLevel.read_only, CaseAccessLevel.full_access)
def export_case(caseid):
    return response_success('', data=export_case_json(caseid))


@case_blueprint.route('/case/tasklog/add', methods=['POST'])
@ac_api_case_requires(CaseAccessLevel.full_access)
def case_add_tasklog(caseid):

    log_schema = TaskLogSchema()

    try:

        log = log_schema.load(request.get_json())

        ua = track_activity(log.get('log_content'), caseid, user_input=True)

    except marshmallow.exceptions.ValidationError as e:
        return response_error(msg="Data error", data=e.messages, status=400)

    return response_success("Log saved", data=ua)


@case_blueprint.route('/case/users/list', methods=['GET'])
@ac_api_case_requires(CaseAccessLevel.read_only, CaseAccessLevel.full_access)
def case_get_users(caseid):

    users = get_users_list_restricted_from_case(caseid)

    return response_success(data=users)


@case_blueprint.route('/case/update-status', methods=['POST'])
@ac_api_case_requires(CaseAccessLevel.full_access)
def case_update_status(caseid):

    case = get_case(caseid)
    if not case:
        return response_error('Invalid case ID')

    status = request.get_json().get('status_id')
    case_status = set(item.value for item in CaseStatus)

    try:
        status = int(status)
    except ValueError:
        return response_error('Invalid status')

    if status not in case_status:
        return response_error('Invalid status')

    case.status_id = status
    db.session.commit()

    return response_success("Case status updated", data=case.status_id)



