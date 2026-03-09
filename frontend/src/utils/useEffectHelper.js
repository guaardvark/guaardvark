import React from 'react';

// Development utility to help detect unsafe useEffect patterns
// This is not a complete solution but helps catch common issues

export const useEffectHelper = {
  // Check if useEffect has missing dependencies
  checkDependencies: (effectName, dependencies, usedValues) => {
    if (process.env.NODE_ENV !== 'development') return;
    
    const missing = usedValues.filter(value => 
      !dependencies.includes(value) && 
      typeof value !== 'undefined'
    );
    
    if (missing.length > 0) {
      console.warn(
        `useEffect "${effectName}" may have missing dependencies:`,
        missing
      );
    }
  },

  // Check for potential infinite loops
  checkForInfiniteLoop: (effectName, dependencies) => {
    if (process.env.NODE_ENV !== 'development') return;
    
    const dangerousPatterns = [
      'setInterval',
      'setTimeout', 
      'addEventListener',
      'removeEventListener'
    ];
    
    if (dependencies.length === 0) {
      console.warn(
        `useEffect "${effectName}" has empty dependency array. Make sure this is intentional.`
      );
    }
  },

  // Common patterns to avoid
  commonMistakes: {
    // Missing function dependencies
    missingFunctionDeps: `
      Bad:
      useEffect(() => {
        fetchData();
      }, []); // fetchData missing from deps
      
      Good:
      useEffect(() => {
        fetchData();
      }, [fetchData]);
    `,
    
    // Missing state dependencies
    missingStateDeps: `
      Bad:
      useEffect(() => {
        if (isLoading) {
          doSomething();
        }
      }, []); // isLoading missing from deps
      
      Good:
      useEffect(() => {
        if (isLoading) {
          doSomething();
        }
      }, [isLoading]);
    `,
    
    // Missing prop dependencies
    missingPropDeps: `
      Bad:
      useEffect(() => {
        processData(data);
      }, []); // data prop missing from deps
      
      Good:
      useEffect(() => {
        processData(data);
      }, [data]);
    `
  }
};

// Hook to validate useEffect usage
export const useValidatedEffect = (effect, deps, debugName) => {
  if (process.env.NODE_ENV === 'development') {
    // Basic validation
    if (deps && deps.length > 0) {
      useEffectHelper.checkForInfiniteLoop(debugName, deps);
    }
  }
  
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return React.useEffect(effect, deps);
};

export default useEffectHelper; 