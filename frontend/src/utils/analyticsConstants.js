/**
 * Analytics Event Constants
 *
 * Centralized event names and categories for consistent tracking
 */

// Event Categories
export const EVENT_CATEGORIES = {
  TASK: 'task',
  OUTPUT: 'output',
  NAVIGATION: 'navigation',
  USER_INTERACTION: 'user_interaction',
  API: 'api',
  PERFORMANCE: 'performance',
  ERROR: 'error',
};

// Task Events
export const TASK_EVENTS = {
  CREATED: 'task_created',
  UPDATED: 'task_updated',
  DELETED: 'task_deleted',
  STARTED: 'task_started',
  STOPPED: 'task_stopped',
  QUEUED: 'task_queued',
  VIEWED: 'task_viewed',
  FILTERED: 'task_filtered',
  SORTED: 'task_sorted',
  SEARCHED: 'task_searched',
};

// Output Events
export const OUTPUT_EVENTS = {
  VIEWED: 'output_viewed',
  DOWNLOADED: 'output_downloaded',
  RETRIED: 'output_retried',
  DELETED: 'output_deleted',
  FILTERED: 'output_filtered',
  SORTED: 'output_sorted',
  SEARCHED: 'output_searched',
  MODAL_OPENED: 'output_modal_opened',
  MODAL_CLOSED: 'output_modal_closed',
};

// Navigation Events
export const NAVIGATION_EVENTS = {
  PAGE_VIEW: 'page_view',
  TAB_CHANGED: 'tab_changed',
  ROUTE_CHANGED: 'route_changed',
};

// User Interaction Events
export const INTERACTION_EVENTS = {
  BUTTON_CLICKED: 'button_clicked',
  FORM_SUBMITTED: 'form_submitted',
  DROPDOWN_CHANGED: 'dropdown_changed',
  CHECKBOX_TOGGLED: 'checkbox_toggled',
  INPUT_CHANGED: 'input_changed',
  MODAL_OPENED: 'modal_opened',
  MODAL_CLOSED: 'modal_closed',
};

// API Events
export const API_EVENTS = {
  CALL_START: 'api_call_start',
  CALL_SUCCESS: 'api_call_success',
  CALL_ERROR: 'api_call_error',
  CALL_TIMEOUT: 'api_call_timeout',
};

// Performance Events
export const PERFORMANCE_EVENTS = {
  PAGE_LOAD: 'page_load_performance',
  COMPONENT_RENDER: 'component_render',
  API_RESPONSE_TIME: 'api_response_time',
  RESOURCE_LOADED: 'resource_loaded',
};

// Error Events
export const ERROR_EVENTS = {
  API_ERROR: 'api_error',
  RUNTIME_ERROR: 'runtime_error',
  VALIDATION_ERROR: 'validation_error',
  NETWORK_ERROR: 'network_error',
};

// Component Names
export const COMPONENTS = {
  TASK_PAGE: 'TaskPage',
  TASK_CARD: 'TaskCard',
  OUTPUT_CARD: 'OutputCard',
  OUTPUT_MODAL: 'OutputModal',
  TASK_FORM: 'TaskForm',
  TASK_TABLE: 'TaskTable',
};

// Action Types
export const ACTIONS = {
  // Task actions
  CREATE_TASK: 'create_task',
  EDIT_TASK: 'edit_task',
  DELETE_TASK: 'delete_task',
  START_TASK: 'start_task',
  STOP_TASK: 'stop_task',
  QUEUE_TASKS: 'queue_tasks',
  VIEW_TASK: 'view_task',

  // Output actions
  VIEW_OUTPUT: 'view_output',
  DOWNLOAD_CSV: 'download_csv',
  DOWNLOAD_XML: 'download_xml',
  RETRY_OUTPUT: 'retry_output',
  DELETE_OUTPUT: 'delete_output',

  // Navigation actions
  SWITCH_TAB: 'switch_tab',
  NAVIGATE: 'navigate',

  // Filter/Sort actions
  APPLY_FILTER: 'apply_filter',
  CLEAR_FILTER: 'clear_filter',
  SORT: 'sort',
  SEARCH: 'search',

  // UI actions
  OPEN_MODAL: 'open_modal',
  CLOSE_MODAL: 'close_modal',
  TOGGLE_VIEW: 'toggle_view',
  REFRESH: 'refresh',
};

// Download Formats
export const DOWNLOAD_FORMATS = {
  CSV: 'csv',
  XML: 'xml',
  JSON: 'json',
  PDF: 'pdf',
};

// Filter Types
export const FILTER_TYPES = {
  PROJECT: 'project',
  MODEL: 'model',
  STATUS: 'status',
  DATE: 'date',
  SEARCH: 'search',
};

// Sort Options
export const SORT_OPTIONS = {
  DATE_ASC: 'date_asc',
  DATE_DESC: 'date_desc',
  NAME_ASC: 'name_asc',
  NAME_DESC: 'name_desc',
  STATUS_ASC: 'status_asc',
  STATUS_DESC: 'status_desc',
};

// Tab Names
export const TABS = {
  TASKS: 'tasks',
  OUTPUTS: 'outputs',
  SETTINGS: 'settings',
  DASHBOARD: 'dashboard',
};

// Error Severity Levels
export const ERROR_SEVERITY = {
  LOW: 'low',
  MEDIUM: 'medium',
  HIGH: 'high',
  CRITICAL: 'critical',
};

// API Endpoints (for tracking)
export const API_ENDPOINTS = {
  TASKS: '/api/tasks',
  OUTPUTS: '/api/outputs',
  PROJECTS: '/api/projects',
  MODELS: '/api/models',
  TRACKING: '/api/tracking',
};

export default {
  EVENT_CATEGORIES,
  TASK_EVENTS,
  OUTPUT_EVENTS,
  NAVIGATION_EVENTS,
  INTERACTION_EVENTS,
  API_EVENTS,
  PERFORMANCE_EVENTS,
  ERROR_EVENTS,
  COMPONENTS,
  ACTIONS,
  DOWNLOAD_FORMATS,
  FILTER_TYPES,
  SORT_OPTIONS,
  TABS,
  ERROR_SEVERITY,
  API_ENDPOINTS,
};
