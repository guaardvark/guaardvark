import React, { useState, useRef, useEffect, memo } from 'react';
import { Box } from '@mui/material';

const OverlayLayer = memo(({
  textElements,
  selectedTextId,
  onSelectText,
  onMoveText,
  containerRef
}) => {
  const [dragState, setDragState] = useState(null);
  const dragRef = useRef(null);

  // We keep a ref to the latest onMoveText so we don't have to add it to useEffect dependencies,
  // which might cause re-binding if the parent doesn't memoize the callback.
  const onMoveTextRef = useRef(onMoveText);
  useEffect(() => {
    onMoveTextRef.current = onMoveText;
  }, [onMoveText]);

  useEffect(() => {
    if (!dragState) return;

    const handleMouseMove = (e) => {
      if (!dragRef.current) return;
      const { startX, startY, originX, originY } = dragRef.current;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      setDragState(prev => prev ? { ...prev, x: originX + dx, y: originY + dy } : null);
    };

    const handleMouseUp = (e) => {
      if (dragRef.current && dragState) {
        const { startX, startY, originX, originY, id } = dragRef.current;
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        onMoveTextRef.current(id, originX + dx, originY + dy);
      }
      setDragState(null);
      dragRef.current = null;
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [dragState]);

  return (
    <>
      {textElements.map((t) => {
        const isDragging = dragState?.id === t.id;
        const renderX = isDragging ? dragState.x : t.x;
        const renderY = isDragging ? dragState.y : t.y;

        return (
          <Box
            key={t.id}
            data-testid={`overlay-text-${t.id}`}
            onMouseDown={(e) => {
              e.stopPropagation();
              onSelectText(t.id);
              dragRef.current = {
                id: t.id,
                startX: e.clientX,
                startY: e.clientY,
                originX: t.x,
                originY: t.y,
              };
              setDragState({ id: t.id, x: t.x, y: t.y });
            }}
            sx={{
              position: "absolute",
              left: `${renderX}px`,
              top: `${renderY}px`,
              transform: `rotate(${t.rotation}deg)`,
              color: t.fontColor,
              fontSize: `${t.fontSize}px`,
              fontWeight: 700,
              textShadow: "1px 1px 3px rgba(0,0,0,0.8)",
              cursor: "move",
              border: selectedTextId === t.id
                ? "1px dashed yellow" : "1px dashed transparent",
              padding: "2px 4px",
              userSelect: "none",
            }}
          >
            {t.text}
          </Box>
        );
      })}
    </>
  );
});

export default OverlayLayer;
