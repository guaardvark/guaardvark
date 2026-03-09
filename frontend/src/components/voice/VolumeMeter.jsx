import React from 'react';
import PropTypes from 'prop-types';

/**
 * VolumeMeter Component
 * Displays a visual volume level indicator
 */
const VolumeMeter = ({ 
  volume = 0, 
  isRecording = false,
  orientation = 'vertical', // 'vertical' or 'horizontal'
  size = 'medium', // 'small', 'medium', 'large'
  color = 'blue',
  showLabel = true,
  className = ''
}) => {
  // Size configurations
  const sizeConfig = {
    small: {
      width: orientation === 'vertical' ? 20 : 100,
      height: orientation === 'vertical' ? 100 : 20,
      fontSize: 'text-xs'
    },
    medium: {
      width: orientation === 'vertical' ? 30 : 150,
      height: orientation === 'vertical' ? 150 : 30,
      fontSize: 'text-sm'
    },
    large: {
      width: orientation === 'vertical' ? 40 : 200,
      height: orientation === 'vertical' ? 200 : 40,
      fontSize: 'text-base'
    }
  };

  // Color configurations
  const colorConfig = {
    blue: {
      bg: 'bg-blue-100',
      low: 'bg-blue-400',
      mid: 'bg-blue-500',
      high: 'bg-blue-600'
    },
    green: {
      bg: 'bg-green-100',
      low: 'bg-green-400',
      mid: 'bg-green-500',
      high: 'bg-green-600'
    },
    red: {
      bg: 'bg-red-100',
      low: 'bg-red-400',
      mid: 'bg-red-500',
      high: 'bg-red-600'
    },
    gradient: {
      bg: 'bg-gray-100',
      low: 'bg-green-400',
      mid: 'bg-yellow-400',
      high: 'bg-red-400'
    }
  };

  const config = sizeConfig[size];
  const colors = colorConfig[color];

  // Calculate volume level (0-100) with NaN protection
  const safeVolume = (volume || 0);
  const volumeLevel = Math.round(safeVolume * 100);
  
  // Determine color based on volume level
  const getVolumeColor = () => {
    if (color === 'gradient') {
      if (safeVolume < 0.3) return colors.low;
      if (safeVolume < 0.7) return colors.mid;
      return colors.high;
    }
    return colors.mid;
  };

  // Calculate fill percentage with additional validation
  const fillPercentage = Math.min(100, Math.max(0, isNaN(volumeLevel) ? 0 : volumeLevel));

  return (
    <div className={`volume-meter ${className}`}>
      {showLabel && (
        <div className={`text-center mb-2 ${config.fontSize} font-medium text-gray-600`}>
          Volume: {volumeLevel}%
        </div>
      )}
      
      <div 
        className={`relative rounded-lg border-2 border-gray-200 ${colors.bg} overflow-hidden`}
        style={{ 
          width: `${config.width}px`, 
          height: `${config.height}px` 
        }}
      >
        {/* Volume fill */}
        <div
          className={`absolute transition-all duration-100 ease-out ${getVolumeColor()}`}
          style={{
            width: orientation === 'vertical' ? '100%' : `${fillPercentage}%`,
            height: orientation === 'vertical' ? `${fillPercentage}%` : '100%',
            [orientation === 'vertical' ? 'bottom' : 'left']: 0,
          }}
        />
        
        {/* Volume level markers */}
        {orientation === 'vertical' ? (
          <div className="absolute inset-0 flex flex-col justify-between py-1">
            {[100, 75, 50, 25].map((level) => (
              <div
                key={level}
                className="w-full h-px bg-gray-300 opacity-50"
                style={{ 
                  bottom: `${level}%`,
                  position: 'absolute'
                }}
              />
            ))}
          </div>
        ) : (
          <div className="absolute inset-0 flex justify-between items-center px-1">
            {[25, 50, 75, 100].map((level) => (
              <div
                key={level}
                className="w-px h-full bg-gray-300 opacity-50"
                style={{ 
                  left: `${level}%`,
                  position: 'absolute'
                }}
              />
            ))}
          </div>
        )}
        
        {/* Recording indicator */}
        {isRecording && (
          <div className="absolute top-1 right-1">
            <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
          </div>
        )}
      </div>
      
      {/* Volume level text */}
      {showLabel && (
        <div className={`text-center mt-1 ${config.fontSize} text-gray-500`}>
          {isRecording ? 'Recording' : 'Ready'}
        </div>
      )}
    </div>
  );
};

VolumeMeter.propTypes = {
  volume: PropTypes.number,
  isRecording: PropTypes.bool,
  orientation: PropTypes.oneOf(['vertical', 'horizontal']),
  size: PropTypes.oneOf(['small', 'medium', 'large']),
  color: PropTypes.oneOf(['blue', 'green', 'red', 'gradient']),
  showLabel: PropTypes.bool,
  className: PropTypes.string,
};

export default VolumeMeter; 