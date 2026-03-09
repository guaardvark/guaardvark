import { useState, useCallback } from 'react';

/**
 * Custom hook for managing notifications and user feedback
 * Provides consistent notification patterns across the application
 */
export const useNotification = () => {
  const [notifications, setNotifications] = useState([]);

  const addNotification = useCallback((notification) => {
    const id = Date.now() + Math.random();
    const newNotification = {
      id,
      timestamp: new Date().toISOString(),
      severity: 'info',
      autoHide: true,
      duration: 4000,
      ...notification
    };

    setNotifications(prev => [...prev, newNotification]);

    // Auto-remove notification if autoHide is enabled
    if (newNotification.autoHide) {
      setTimeout(() => {
        removeNotification(id);
      }, newNotification.duration);
    }

    return id;
  }, []);

  const removeNotification = useCallback((id) => {
    setNotifications(prev => prev.filter(n => n.id !== id));
  }, []);

  const clearAllNotifications = useCallback(() => {
    setNotifications([]);
  }, []);

  // Convenience methods for different notification types
  const showSuccess = useCallback((message, options = {}) => {
    return addNotification({
      message,
      severity: 'success',
      ...options
    });
  }, [addNotification]);

  const showError = useCallback((message, options = {}) => {
    return addNotification({
      message,
      severity: 'error',
      duration: 6000, // Longer duration for errors
      ...options
    });
  }, [addNotification]);

  const showWarning = useCallback((message, options = {}) => {
    return addNotification({
      message,
      severity: 'warning',
      duration: 5000,
      ...options
    });
  }, [addNotification]);

  const showInfo = useCallback((message, options = {}) => {
    return addNotification({
      message,
      severity: 'info',
      ...options
    });
  }, [addNotification]);

  // Process-specific notifications
  const showProgress = useCallback((message, progress = 0, options = {}) => {
    return addNotification({
      message,
      severity: 'info',
      progress,
      autoHide: false, // Progress notifications don't auto-hide
      showProgress: true,
      ...options
    });
  }, [addNotification]);

  const updateProgress = useCallback((id, progress, message = null) => {
    setNotifications(prev => 
      prev.map(n => 
        n.id === id 
          ? { ...n, progress, ...(message && { message }) }
          : n
      )
    );
  }, []);

  const completeProgress = useCallback((id, successMessage = null) => {
    setNotifications(prev => 
      prev.map(n => 
        n.id === id 
          ? { 
              ...n, 
              progress: 100, 
              severity: 'success',
              message: successMessage || n.message,
              autoHide: true,
              duration: 3000
            }
          : n
      )
    );

    // Remove after showing success
    setTimeout(() => removeNotification(id), 3000);
  }, [removeNotification]);

  const failProgress = useCallback((id, errorMessage = null) => {
    setNotifications(prev => 
      prev.map(n => 
        n.id === id 
          ? { 
              ...n, 
              severity: 'error',
              message: errorMessage || 'Operation failed',
              showProgress: false,
              autoHide: true,
              duration: 6000
            }
          : n
      )
    );

    // Remove after showing error
    setTimeout(() => removeNotification(id), 6000);
  }, [removeNotification]);

  // Batch operations
  const showBatchOperation = useCallback((operationName, totalItems) => {
    return addNotification({
      message: `${operationName}: Processing ${totalItems} items...`,
      severity: 'info',
      progress: 0,
      showProgress: true,
      autoHide: false,
      batchInfo: {
        operationName,
        totalItems,
        completedItems: 0,
        failedItems: 0
      }
    });
  }, [addNotification]);

  const updateBatchProgress = useCallback((id, completedItems, failedItems = 0) => {
    setNotifications(prev => 
      prev.map(n => {
        if (n.id === id && n.batchInfo) {
          const { totalItems, operationName } = n.batchInfo;
          const progress = (completedItems / totalItems) * 100;
          const newBatchInfo = {
            ...n.batchInfo,
            completedItems,
            failedItems
          };
          
          return {
            ...n,
            progress,
            batchInfo: newBatchInfo,
            message: `${operationName}: ${completedItems}/${totalItems} completed${failedItems > 0 ? ` (${failedItems} failed)` : ''}`
          };
        }
        return n;
      })
    );
  }, []);

  const completeBatchOperation = useCallback((id, summary = null) => {
    setNotifications(prev => 
      prev.map(n => {
        if (n.id === id && n.batchInfo) {
          const { operationName, totalItems, completedItems, failedItems } = n.batchInfo;
          const hasFailures = failedItems > 0;
          
          return {
            ...n,
            progress: 100,
            severity: hasFailures ? 'warning' : 'success',
            message: summary || `${operationName} completed: ${completedItems}/${totalItems} successful${hasFailures ? `, ${failedItems} failed` : ''}`,
            showProgress: false,
            autoHide: true,
            duration: hasFailures ? 6000 : 4000
          };
        }
        return n;
      })
    );

    // Remove after showing completion
    setTimeout(() => removeNotification(id), 5000);
  }, [removeNotification]);

  return {
    notifications,
    addNotification,
    removeNotification,
    clearAllNotifications,
    
    // Basic notifications
    showSuccess,
    showError,
    showWarning,
    showInfo,
    
    // Progress notifications
    showProgress,
    updateProgress,
    completeProgress,
    failProgress,
    
    // Batch operations
    showBatchOperation,
    updateBatchProgress,
    completeBatchOperation
  };
}; 