from flask import Blueprint, request, jsonify, current_app
from backend.services.orchestrator_service import get_orchestrator
import logging

orchestrator_bp = Blueprint('orchestrator', __name__, url_prefix='/api/orchestrator')
logger = logging.getLogger(__name__)

@orchestrator_bp.route('/plan', methods=['POST'])
def create_plan():
    """
    Create a new orchestration plan from a user request.
    """
    try:
        data = request.get_json()
        if not data or 'request' not in data:
            return jsonify({'error': 'Missing "request" field'}), 400
            
        user_request = data['request']
        context = data.get('context', {})
        
        orchestrator = get_orchestrator()
        plan = orchestrator._create_plan(user_request)
        
        # Store plan in memory (simple version)
        # In a real app, this should be in the database
        plan_id = str(id(plan)) # Simple ID for now
        orchestrator._active_plans[plan_id] = plan
        
        return jsonify({
            'success': True,
            'plan_id': plan_id,
            'plan': orchestrator._serialize_plan(plan)
        })
        
    except Exception as e:
        logger.error(f"Error creating plan: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@orchestrator_bp.route('/execute', methods=['POST'])
def execute_plan():
    """
    Execute an existing plan.
    """
    try:
        data = request.get_json()
        if not data or 'plan_id' not in data:
            return jsonify({'error': 'Missing "plan_id" field'}), 400
            
        plan_id = data['plan_id']
        context = data.get('context', {})
        
        orchestrator = get_orchestrator()
        plan = orchestrator._active_plans.get(plan_id)
        
        if not plan:
            return jsonify({'error': 'Plan not found'}), 404
            
        # Execute in background? For now, sync for simplicity, but ideally async
        # We can use Celery here later.
        
        result = orchestrator._execute_plan(plan, context)
        
        return jsonify({
            'success': True,
            'result': result
        })
        
    except Exception as e:
        logger.error(f"Error executing plan: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@orchestrator_bp.route('/status/<plan_id>', methods=['GET'])
def get_plan_status(plan_id):
    """
    Get the status of a plan.
    """
    try:
        orchestrator = get_orchestrator()
        plan = orchestrator._active_plans.get(plan_id)
        
        if not plan:
            return jsonify({'error': 'Plan not found'}), 404
            
        return jsonify({
            'success': True,
            'plan': orchestrator._serialize_plan(plan)
        })
        
    except Exception as e:
        logger.error(f"Error getting plan status: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
