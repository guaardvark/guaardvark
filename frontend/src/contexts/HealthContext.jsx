import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { getCeleryHealth, getBackendHealth, getDbHealth, getRedisHealth } from '../api';

const HealthContext = createContext();

export const useHealth = () => {
  const context = useContext(HealthContext);
  if (!context) {
    throw new Error('useHealth must be used within a HealthProvider');
  }
  return context;
};

export const HealthProvider = ({ children }) => {
  const [healthData, setHealthData] = useState({
    backend: null,
    db: null,
    celery: null,
    redis: null,
    lastUpdated: null,
    isLoading: false,
    errors: {}
  });

  const [subscribers, setSubscribers] = useState(new Set());

  const updateHealthData = useCallback(async (force = false) => {
    const now = Date.now();
    const cacheTimeout = 30000; // 30 seconds cache

    // Skip if not forced and cache is still valid
    if (!force && healthData.lastUpdated && (now - healthData.lastUpdated) < cacheTimeout) {
      return healthData;
    }

    setHealthData(prev => ({ ...prev, isLoading: true }));

    const results = await Promise.allSettled([
      getBackendHealth(),
      getDbHealth(),
      getCeleryHealth(),
      getRedisHealth()
    ]);

    const [backendRes, dbRes, celeryRes, redisRes] = results;

    const newData = {
      backend: backendRes.status === 'fulfilled' ? backendRes.value : null,
      db: dbRes.status === 'fulfilled' ? dbRes.value : null,
      celery: celeryRes.status === 'fulfilled' ? celeryRes.value : null,
      redis: redisRes.status === 'fulfilled' ? redisRes.value : null,
      lastUpdated: now,
      isLoading: false,
      errors: {
        backend: backendRes.status === 'rejected' ? backendRes.reason?.message : null,
        db: dbRes.status === 'rejected' ? dbRes.reason?.message : null,
        celery: celeryRes.status === 'rejected' ? celeryRes.reason?.message : null,
        redis: redisRes.status === 'rejected' ? redisRes.reason?.message : null
      }
    };

    setHealthData(newData);
    return newData;
  }, [healthData.lastUpdated]);

  // Subscribe/unsubscribe mechanism for components
  const subscribe = useCallback((callback) => {
    setSubscribers(prev => new Set([...prev, callback]));
    return () => {
      setSubscribers(prev => {
        const newSet = new Set(prev);
        newSet.delete(callback);
        return newSet;
      });
    };
  }, []);

  // Notify subscribers when health data changes
  useEffect(() => {
    subscribers.forEach(callback => callback(healthData));
  }, [healthData, subscribers]);

  const value = {
    healthData,
    updateHealthData,
    subscribe,
    // Convenience getters
    getCeleryHealth: () => healthData.celery,
    getBackendHealth: () => healthData.backend,
    getDbHealth: () => healthData.db,
    getRedisHealth: () => healthData.redis,
    getErrors: () => healthData.errors,
    isLoading: healthData.isLoading,
    lastUpdated: healthData.lastUpdated
  };

  return (
    <HealthContext.Provider value={value}>
      {children}
    </HealthContext.Provider>
  );
};
