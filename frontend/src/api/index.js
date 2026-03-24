// frontend/src/api/index.js
// Version 2.2: Fixed conflicting exports - getProjectsForClient only exported from projectService
// Export commonly used taskService functions statically (processTaskQueue remains dynamic)
export * from "./apiClient";
export * from "./documentService";
export * from "./websiteService";
// taskService functions exported individually - processTaskQueue remains dynamically imported
export {
  getTasks,
  createTask,
  updateTask,
  deleteTask,
  duplicateTask,
  getDefaultTaskModel,
  setDefaultTaskModel,
  reprocessTask,
  startTask,
} from "./taskService";
export * from "./ruleService";
export * from "./modelService";
// indexingService is not exported here to allow dynamic imports for code splitting
// Import directly from "./indexingService" when needed
export * from "./trainingService";
export * from "./chatService";
export * from "./stateService";
export * from "./settingsService";
export * from "./backupService";
export * from "./csvService";
export * from "./utilService";
export * from "./filegenService";
export * from "./devtoolsService";
export * from "./progressService";

// Export projectService (includes getProjectsForClient)
export * from "./projectService";

// Export clientService but exclude getProjectsForClient to avoid conflict
export {
  getClients,
  createClient,
  updateClient,
  uploadClientLogo,
  deleteClient,
} from "./clientService";

// Export WordPress service
export * from "./wordpressService";
export * from "./orchestratorService";
export * from "./gpuService";
