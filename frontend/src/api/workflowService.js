// frontend/src/api/workflowService.js
// Industrial Workflow Service - Connects UI to Bulk Processing Infrastructure
// Version 1.0: Dynamic client-based workflow management with recovery capabilities

import { BASE_URL, handleResponse } from "./apiClient";

/**
 * Create an industrial workflow that connects to bulk processing systems
 * @param {Object} workflowData - Workflow configuration
 * @param {string} workflowData.type - Type of workflow (bulk_content_generation, website_analysis, etc.)
 * @param {string} workflowData.name - Workflow name
 * @param {string} workflowData.description - Workflow description
 * @param {number} workflowData.clientId - Client ID from database
 * @param {number} workflowData.projectId - Project ID from database (optional)
 * @param {number} workflowData.quantity - Number of items to process
 * @param {number} workflowData.targetWordCount - Target word count per item
 * @param {string} workflowData.outputFilename - Output filename (optional)
 * @param {number} workflowData.priority - Priority (1=High, 2=Medium, 3=Low)
 * @returns {Promise<Object>} Created workflow with job tracking
 */
export const createIndustrialWorkflow = async (workflowData) => {
  try {
    console.log('Creating industrial workflow:', workflowData);
    
    // Get client name for prompt generation
    const clientName = workflowData.clientName || 'Client';
    
    // Use unified job creation endpoint
    const endpoint = '/jobs/create';
    
    // Create unified payload with workflow type-specific configuration
    const payload = {
      // Common job metadata
      name: workflowData.name,
      description: workflowData.description,
      type: workflowData.type,
      priority: workflowData.priority || 2,
      client_id: workflowData.clientId,
      project_id: workflowData.projectId,
      model_name: workflowData.modelName || '',
      status: 'pending',
      
      // Workflow-specific configuration
      workflow_config: generateWorkflowConfig(workflowData, clientName),
      
      // Progress tracking metadata
      metadata: {
        created_by: 'TaskPage',
        client_name: clientName,
        quantity: workflowData.quantity,
        target_word_count: workflowData.targetWordCount,
        output_filename: workflowData.outputFilename,
      }
    };
    
    const response = await fetch(`${BASE_URL}${endpoint}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
    
    const data = await handleResponse(response);
    
    console.log('Industrial workflow created:', data);
    return data;
    
  } catch (error) {
    console.error('Error creating industrial workflow:', error);
    throw error;
  }
};

/**
 * Generate workflow-specific configuration for different job types
 * @param {Object} workflowData - Workflow configuration
 * @param {string} clientName - Actual client name
 * @returns {Object} Workflow configuration object
 */
const generateWorkflowConfig = (workflowData, clientName) => {
  const baseConfig = {
    client_name: clientName,
    quantity: workflowData.quantity,
    target_word_count: workflowData.targetWordCount,
    prompt_text: generatePromptFromWorkflow(workflowData, clientName),
  };
  
  switch (workflowData.type) {
    case 'bulk_content_generation':
      return {
        ...baseConfig,
        execution_type: 'bulk_csv_generation',
        output_filename: workflowData.outputFilename || `bulk_content_${clientName.toLowerCase().replace(/\s+/g, '_')}_${Date.now()}.csv`,
        context_template: 'generic',
        context_variables: {
          client: clientName,
          project: workflowData.description || 'Content Generation',
          website: 'professional-website.com',
          quantity: workflowData.quantity,
        },
        concurrent_workers: Math.min(15, Math.max(5, Math.floor(workflowData.quantity / 20))),
        batch_size: Math.min(50, Math.max(10, Math.floor(workflowData.quantity / 10))),
        // Add bulk generation specific parameters
        topics: 'auto', // Auto-generate topics based on client/project
        resume_from_id: null, // For resuming interrupted generations
      };
      
    case 'website_analysis':
      return {
        ...baseConfig,
        execution_type: 'website_analysis',
        analysis_type: 'comprehensive',
        include_content: true,
        include_seo: true,
      };
      
    case 'sequential_processing':
      return {
        ...baseConfig,
        execution_type: 'sequential_tasks',
        auto_progression: true,
      };
      
    default:
      return {
        ...baseConfig,
        execution_type: 'standard_task',
      };
  }
};

/**
 * Generate a dynamic prompt based on workflow data and client information
 * @param {Object} workflowData - Workflow configuration
 * @param {string} clientName - Actual client name
 * @returns {string} Generated prompt text
 */
const generatePromptFromWorkflow = (workflowData, clientName) => {
  const basePrompts = {
    bulk_content_generation: `Generate ${workflowData.quantity} high-quality content pieces for ${clientName}. Each piece should be approximately ${workflowData.targetWordCount} words and tailored to their industry and target audience. Include relevant keywords, meta descriptions, and call-to-action elements.`,
    
    website_analysis: `Perform a comprehensive analysis of ${clientName}'s website. Include SEO analysis, content audit, user experience review, and competitive positioning. Provide actionable recommendations for improvement.`,
    
    sequential_processing: `Process multiple content generation tasks in sequence for ${clientName}. Start with ${workflowData.quantity} items, then move to the next phase based on completion status. Maintain consistency across all generated content.`,
    
    default: `Execute ${workflowData.type.replace('_', ' ')} workflow for ${clientName}. Process ${workflowData.quantity} items with ${workflowData.targetWordCount} words each. Follow established quality standards and client-specific requirements.`
  };
  
  return basePrompts[workflowData.type] || basePrompts.default;
};

/**
 * Start a workflow that's in pending or paused state
 * @param {number} workflowId - Workflow ID
 * @returns {Promise<Object>} Updated workflow status
 */
export const startWorkflow = async (workflowId) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/${workflowId}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ status: 'pending' }),
    });
    
    return await handleResponse(response);
  } catch (error) {
    console.error(`Error starting workflow ${workflowId}:`, error);
    throw error;
  }
};

/**
 * Pause a running workflow
 * @param {number} workflowId - Workflow ID
 * @returns {Promise<Object>} Updated workflow status
 */
export const pauseWorkflow = async (workflowId) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/${workflowId}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ status: 'paused' }),
    });
    
    return await handleResponse(response);
  } catch (error) {
    console.error(`Error pausing workflow ${workflowId}:`, error);
    throw error;
  }
};

/**
 * Stop a workflow completely
 * @param {number} workflowId - Workflow ID
 * @returns {Promise<Object>} Updated workflow status
 */
export const stopWorkflow = async (workflowId) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/${workflowId}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ status: 'cancelled' }),
    });
    
    return await handleResponse(response);
  } catch (error) {
    console.error(`Error stopping workflow ${workflowId}:`, error);
    throw error;
  }
};

/**
 * Reprocess a failed or completed workflow
 * @param {number} workflowId - Workflow ID
 * @returns {Promise<Object>} Reprocessing result
 */
export const reprocessWorkflow = async (workflowId) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/${workflowId}/reprocess`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
    });
    
    return await handleResponse(response);
  } catch (error) {
    console.error(`Error reprocessing workflow ${workflowId}:`, error);
    throw error;
  }
};

/**
 * Delete a workflow
 * @param {number} workflowId - Workflow ID
 * @returns {Promise<Object>} Deletion result
 */
export const deleteWorkflow = async (workflowId) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/${workflowId}`, {
      method: 'DELETE',
    });
    
    return await handleResponse(response);
  } catch (error) {
    console.error(`Error deleting workflow ${workflowId}:`, error);
    throw error;
  }
};

/**
 * Get workflow progress and details
 * @param {number} workflowId - Workflow ID
 * @returns {Promise<Object>} Workflow details with progress
 */
export const getWorkflowProgress = async (workflowId) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/${workflowId}`);
    return await handleResponse(response);
  } catch (error) {
    console.error(`Error getting workflow progress ${workflowId}:`, error);
    throw error;
  }
};

/**
 * Process the entire workflow queue
 * @returns {Promise<Object>} Queue processing result
 */
export const processWorkflowQueue = async () => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/process-queue`, {
      method: 'POST',
    });
    
    return await handleResponse(response);
  } catch (error) {
    console.error('Error processing workflow queue:', error);
    throw error;
  }
};

/**
 * Get all workflows with optional filtering
 * @param {Object} filters - Optional filters
 * @param {number} filters.clientId - Filter by client ID
 * @param {number} filters.projectId - Filter by project ID
 * @param {string} filters.status - Filter by status
 * @param {string} filters.type - Filter by workflow type
 * @returns {Promise<Array>} List of workflows
 */
export const getWorkflows = async (filters = {}) => {
  try {
    const params = new URLSearchParams();
    
    if (filters.clientId) params.append('client_id', filters.clientId);
    if (filters.projectId) params.append('project_id', filters.projectId);
    if (filters.status) params.append('status', filters.status);
    if (filters.type) params.append('type', filters.type);
    
    const queryString = params.toString();
    const url = `${BASE_URL}/tasks${queryString ? `?${queryString}` : ''}`;
    
    const response = await fetch(url);
    return await handleResponse(response);
    
  } catch (error) {
    console.error('Error getting workflows:', error);
    throw error;
  }
};

/**
 * Create a sequential workflow that processes multiple clients automatically
 * @param {Array} clientWorkflows - Array of workflow configurations for different clients
 * @returns {Promise<Object>} Sequential workflow creation result
 */
export const createSequentialWorkflow = async (clientWorkflows) => {
  try {
    console.log('Creating sequential workflow for multiple clients:', clientWorkflows);
    
    // Create individual workflows with dependencies
    const createdWorkflows = [];
    
    for (let i = 0; i < clientWorkflows.length; i++) {
      const workflowConfig = {
        ...clientWorkflows[i],
        name: `${clientWorkflows[i].name} (Sequence ${i + 1}/${clientWorkflows.length})`,
        priority: 2, // Medium priority for sequential processing
        dependencies: i > 0 ? [createdWorkflows[i - 1].id] : [], // Depend on previous workflow
      };
      
      const workflow = await createIndustrialWorkflow(workflowConfig);
      createdWorkflows.push(workflow);
    }
    
    return {
      success: true,
      message: `Created sequential workflow with ${createdWorkflows.length} stages`,
      workflows: createdWorkflows,
      total_items: clientWorkflows.reduce((sum, w) => sum + (w.quantity || 0), 0),
    };
    
  } catch (error) {
    console.error('Error creating sequential workflow:', error);
    throw error;
  }
};

/**
 * Update an existing workflow/task
 * @param {number} workflowId - Workflow ID to update
 * @param {Object} updateData - Data to update
 * @returns {Promise<Object>} Updated workflow
 */
export const updateWorkflow = async (workflowId, updateData) => {
  try {
    console.log('Updating workflow:', workflowId, updateData);
    
    const response = await fetch(`${BASE_URL}/tasks/${workflowId}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        name: updateData.name,
        description: updateData.description,
        priority: updateData.priority,
        quantity: updateData.quantity,
        target_word_count: updateData.targetWordCount,
        output_filename: updateData.outputFilename,
        model_name: updateData.modelName,
      }),
    });
    
    const data = await handleResponse(response);
    console.log('Workflow updated:', data);
    return data;
    
  } catch (error) {
    console.error(`Error updating workflow ${workflowId}:`, error);
    throw error;
  }
};

/**
 * Get workflow statistics and metrics
 * @returns {Promise<Object>} Workflow statistics
 */
export const getWorkflowStats = async () => {
  try {
    const workflows = await getWorkflows();
    
    const stats = {
      total: workflows.length,
      pending: 0,
      'in-progress': 0,
      completed: 0,
      failed: 0,
      paused: 0,
      total_items_processed: 0,
      estimated_completion_time: 0,
    };
    
    workflows.forEach(workflow => {
      stats[workflow.status] = (stats[workflow.status] || 0) + 1;
      
      // Calculate estimated items processed based on progress
      if (workflow.progress && workflow.quantity) {
        stats.total_items_processed += Math.floor((workflow.progress / 100) * workflow.quantity);
      }
    });
    
    return stats;
    
  } catch (error) {
    console.error('Error getting workflow stats:', error);
    throw error;
  }
}; 