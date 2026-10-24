import { useCallback } from 'react';
import { useAppStore } from '../stores/useAppStore';
import { translate } from '../i18n';

/**
 * Custom hook for centralized error handling and user feedback
 * Provides consistent error handling patterns across the application
 */
export const useErrorHandler = () => {
  const { setError, clearError, setIsLoading } = useAppStore();

  // Generic error handler for API calls
  const handleApiError = useCallback((error, customMessage = null) => {
    let errorMessage = customMessage || translate('messages.errors.unexpected');
    
    if (error?.response) {
      // HTTP error response
      const status = error.response.status;
      const data = error.response.data;
      
      switch (status) {
        case 400:
          errorMessage = data?.message || translate('messages.errors.badRequest');
          break;
        case 401:
          errorMessage = translate('messages.errors.unauthorized');
          break;
        case 403:
          errorMessage = translate('messages.errors.forbidden');
          break;
        case 404:
          errorMessage = translate('messages.errors.notFound');
          break;
        case 422:
          errorMessage = data?.message || translate('messages.errors.validation');
          break;
        case 429:
          errorMessage = translate('messages.errors.tooManyRequests');
          break;
        case 500:
          errorMessage = translate('messages.errors.server');
          break;
        case 503:
          errorMessage = translate('messages.errors.unavailable');
          break;
        default:
          errorMessage = data?.message || translate('messages.errors.httpStatus', { status });
      }
    } else if (error?.message) {
      // Network or other error
      if (error.message.includes('Network Error')) {
        errorMessage = translate('messages.errors.network');
      } else if (error.message.includes('timeout')) {
        errorMessage = translate('messages.errors.timeout');
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
    return handleApiError(error, translate('messages.errors.uploadFailed'));
  }, [handleApiError]);

  const handleIndexingError = useCallback((error) => {
    return handleApiError(error, translate('messages.errors.indexingFailed'));
  }, [handleApiError]);

  const handleChatError = useCallback((error) => {
    return handleApiError(error, translate('messages.errors.chatFailed'));
  }, [handleApiError]);

  const handleProjectError = useCallback((error) => {
    return handleApiError(error, translate('messages.errors.projectFailed'));
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