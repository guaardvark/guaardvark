import { useState, useCallback, useRef, useEffect } from 'react';
import { useErrorHandler } from './useErrorHandler';

/**
 * Custom hook for managing async operations with loading states
 * Provides automatic loading state management and error handling
 */
export const useAsyncOperation = (initialState = null) => {
  const [data, setData] = useState(initialState);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const { handleApiError } = useErrorHandler();
  const mountedRef = useRef(true);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const execute = useCallback(async (asyncFunction, options = {}) => {
    const { 
      skipLoading = false,
      transform = null,
      onSuccess = null,
      onError = null,
      customErrorMessage = null 
    } = options;

    try {
      if (!skipLoading && mountedRef.current) {
        setIsLoading(true);
        setError(null);
      }

      const result = await asyncFunction();
      
      if (!mountedRef.current) return null;

      const finalData = transform ? transform(result) : result;
      setData(finalData);

      if (onSuccess) {
        onSuccess(finalData);
      }

      return finalData;
    } catch (err) {
      if (!mountedRef.current) return null;

      const errorMessage = handleApiError(err, customErrorMessage);
      setError(errorMessage);

      if (onError) {
        onError(err, errorMessage);
      }

      throw err;
    } finally {
      if (!skipLoading && mountedRef.current) {
        setIsLoading(false);
      }
    }
  }, [handleApiError]);

  const reset = useCallback(() => {
    if (mountedRef.current) {
      setData(initialState);
      setIsLoading(false);
      setError(null);
    }
  }, [initialState]);

  const setDataDirect = useCallback((newData) => {
    if (mountedRef.current) {
      setData(newData);
    }
  }, []);

  return {
    data,
    isLoading,
    error,
    execute,
    reset,
    setData: setDataDirect,
  };
};

/**
 * Hook specifically for managing lists with add/remove/update operations
 */
export const useAsyncList = (initialList = []) => {
  const {
    data: items,
    isLoading,
    error,
    execute,
    reset,
    setData
  } = useAsyncOperation(initialList);

  const addItem = useCallback((item) => {
    setData(prevItems => [...prevItems, item]);
  }, [setData]);

  const removeItem = useCallback((itemId, idField = 'id') => {
    setData(prevItems => prevItems.filter(item => item[idField] !== itemId));
  }, [setData]);

  const updateItem = useCallback((itemId, updates, idField = 'id') => {
    setData(prevItems => 
      prevItems.map(item => 
        item[idField] === itemId ? { ...item, ...updates } : item
      )
    );
  }, [setData]);

  const replaceItem = useCallback((newItem, idField = 'id') => {
    setData(prevItems => 
      prevItems.map(item => 
        item[idField] === newItem[idField] ? newItem : item
      )
    );
  }, [setData]);

  return {
    items,
    isLoading,
    error,
    execute,
    reset,
    addItem,
    removeItem,
    updateItem,
    replaceItem,
    setItems: setData,
  };
}; 