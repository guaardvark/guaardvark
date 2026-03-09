import { useCallback } from 'react';
import { useAppStore } from '../stores/useAppStore';

/**
 * Custom hook for centralized error handling and user feedback
 * Provides consistent error handling patterns across the application
 */
export const useErrorHandler = () => {
  const { setError, clearError, setIsLoading } = useAppStore();

  // Generic error handler for API calls
  const handleApiError = useCallback((error, customMessage = null) => {
    let errorMessage = customMessage || 'An unexpected error occurred';
    
    if (error?.response) {
      // HTTP error response
      const status = error.response.status;
      const data = error.response.data;
      
      switch (status) {
        case 400:
          errorMessage = data?.message || 'Bad request. Please check your input.';
          break;
        case 401:
          errorMessage = 'Authentication required. Please log in.';
          break;
        case 403:
          errorMessage = 'You do not have permission to perform this action.';
          break;
        case 404:
          errorMessage = 'The requested resource was not found.';
          break;
        case 422:
          errorMessage = data?.message || 'Validation error. Please check your input.';
          break;
        case 429:
          errorMessage = 'Too many requests. Please try again later.';
          break;
        case 500:
          errorMessage = 'Server error. Please try again later.';
          break;
        case 503:
          errorMessage = 'Service temporarily unavailable. Please try again later.';
          break;
        default:
          errorMessage = data?.message || `HTTP Error ${status}`;
      }
    } else if (error?.message) {
      // Network or other error
      if (error.message.includes('Network Error')) {
        errorMessage = 'Network error. Please check your connection.';
      } else if (error.message.includes('timeout')) {
        errorMessage = 'Request timed out. Please try again.';
      } else {
        errorMessage = error.message;
      }
    }
    
    console.error('API Error:', error);
    setError(errorMessage);
    setIsLoading(false);
    
    return errorMessage;
  }, [setError, setIsLoading]);

  // Wrapper for async operations with error handling
  const withErrorHandling = useCallback(async (
    asyncOperation,
    options = {}
  ) => {
    const { 
      showLoading = true, 
      customErrorMessage = null,
      onSuccess = null,
      onError = null 
    } = options;

    try {
      if (showLoading) {
        setIsLoading(true);
        clearError();
      }

      const result = await asyncOperation();
      
      if (onSuccess) {
        onSuccess(result);
      }
      
      return result;
    } catch (error) {
      const errorMessage = handleApiError(error, customErrorMessage);
      
      if (onError) {
        onError(error, errorMessage);
      }
      
      throw error; // Re-throw to allow component-level handling if needed
    } finally {
      if (showLoading) {
        setIsLoading(false);
      }
    }
  }, [handleApiError, setIsLoading, clearError]);

  // Specialized handlers for common operations
  const handleDocumentUpload = useCallback((error) => {
    return handleApiError(error, 'Failed to upload document. Please try again.');
  }, [handleApiError]);

  const handleIndexingError = useCallback((error) => {
    return handleApiError(error, 'Document indexing failed. Please try again.');
  }, [handleApiError]);

  const handleChatError = useCallback((error) => {
    return handleApiError(error, 'Chat request failed. Please try again.');
  }, [handleApiError]);

  const handleProjectError = useCallback((error) => {
    return handleApiError(error, 'Project operation failed. Please try again.');
  }, [handleApiError]);

  return {
    handleApiError,
    withErrorHandling,
    handleDocumentUpload,
    handleIndexingError,
    handleChatError,
    handleProjectError,
    clearError,
  };
}; 