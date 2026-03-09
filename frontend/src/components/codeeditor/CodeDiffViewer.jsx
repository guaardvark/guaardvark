// frontend/src/components/codeeditor/CodeDiffViewer.jsx
// Component for viewing and applying code changes with diff visualization

import React, { useState, useMemo } from 'react';
import {
  Box,
  Paper,
  Typography,
  Button,
  ButtonGroup,
  Tabs,
  Tab,
  Alert,
  Chip,
  Stack,
  useTheme,
} from '@mui/material';
import {
  Check as AcceptIcon,
  Close as RejectIcon,
  Visibility as PreviewIcon,
  Code as CodeIcon,
  SwapHoriz as DiffIcon,
} from '@mui/icons-material';
import Editor from '@monaco-editor/react';

const CodeDiffViewer = ({
  originalCode = '',
  modifiedCode = '',
  language = 'javascript',
  description = '',
  onAccept,
  onReject,
  onClose,
  files = null, // Multi-file support: [{filePath, originalCode, modifiedCode, language}]
}) => {
  const theme = useTheme();
  const [activeTab, setActiveTab] = useState(0);
  
  // Determine if this is multi-file mode
  const isMultiFile = files && Array.isArray(files) && files.length > 0;
  const currentFile = isMultiFile ? files[activeTab] : null;
  
  // Use file-specific data if in multi-file mode, otherwise use props
  const displayOriginalCode = isMultiFile ? (currentFile?.originalCode || '') : originalCode;
  const displayModifiedCode = isMultiFile ? (currentFile?.modifiedCode || '') : modifiedCode;
  const displayLanguage = isMultiFile ? (currentFile?.language || language) : language;
  const displayDescription = isMultiFile ? (currentFile?.description || description) : description;
  const displayFilePath = isMultiFile ? (currentFile?.filePath || 'untitled') : null;

  // Calculate basic diff statistics
  const diffStats = useMemo(() => {
    const originalLines = displayOriginalCode.split('\n');
    const modifiedLines = displayModifiedCode.split('\n');

    return {
      originalLength: originalLines.length,
      modifiedLength: modifiedLines.length,
      linesAdded: Math.max(0, modifiedLines.length - originalLines.length),
      linesChanged: originalLines.filter((line, index) =>
        modifiedLines[index] && line !== modifiedLines[index]
      ).length,
    };
  }, [displayOriginalCode, displayModifiedCode]);
  
  // Multi-file summary stats
  const multiFileStats = useMemo(() => {
    if (!isMultiFile) return null;
    
    return {
      totalFiles: files.length,
      totalChanges: files.reduce((sum, file) => {
        const orig = file.originalCode?.split('\n') || [];
        const mod = file.modifiedCode?.split('\n') || [];
        return sum + Math.abs(mod.length - orig.length);
      }, 0)
    };
  }, [isMultiFile, files]);

  const handleTabChange = (event, newValue) => {
    setActiveTab(newValue);
  };

  const renderEditor = (code, title, readonly = true) => (
    <Box sx={{ height: '300px', border: 1, borderColor: 'divider', borderRadius: 1 }}>
      <Box sx={{
        p: 1,
        bgcolor: 'action.hover',
        borderBottom: 1,
        borderColor: 'divider',
        borderRadius: '4px 4px 0 0'
      }}>
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 'bold' }}>
          {title}
        </Typography>
      </Box>
      <Editor
        height="270px"
        language={displayLanguage}
        value={code}
        theme={theme.palette.mode === 'dark' ? 'vs-dark' : 'vs-light'}
        options={{
          readOnly: readonly,
          fontSize: 12,
          wordWrap: 'on',
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          automaticLayout: true,
          lineNumbers: 'on',
          renderLineHighlight: 'line',
          selectOnLineNumbers: true,
        }}
      />
    </Box>
  );

  const renderDiffView = () => (
    <Box sx={{ height: '300px', border: 1, borderColor: 'divider', borderRadius: 1 }}>
      <Box sx={{
        p: 1,
        bgcolor: 'action.hover',
        borderBottom: 1,
        borderColor: 'divider',
        borderRadius: '4px 4px 0 0'
      }}>
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 'bold' }}>
          Side-by-Side Comparison{displayFilePath ? ` - ${displayFilePath}` : ''}
        </Typography>
      </Box>
      <Box sx={{ display: 'flex', height: '270px' }}>
        <Box sx={{ flex: 1, borderRight: 1, borderColor: 'divider' }}>
          <Editor
            height="100%"
            language={displayLanguage}
            value={displayOriginalCode}
            theme={theme.palette.mode === 'dark' ? 'vs-dark' : 'vs-light'}
            options={{
              readOnly: true,
              fontSize: 11,
              wordWrap: 'on',
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              automaticLayout: true,
              lineNumbers: 'on',
              renderLineHighlight: 'none',
            }}
          />
        </Box>
        <Box sx={{ flex: 1 }}>
          <Editor
            height="100%"
            language={displayLanguage}
            value={displayModifiedCode}
            theme={theme.palette.mode === 'dark' ? 'vs-dark' : 'vs-light'}
            options={{
              readOnly: true,
              fontSize: 11,
              wordWrap: 'on',
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              automaticLayout: true,
              lineNumbers: 'on',
              renderLineHighlight: 'none',
            }}
          />
        </Box>
      </Box>
    </Box>
  );

  return (
    <Paper
      elevation={3}
      sx={{
        position: 'fixed',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        width: '90%',
        maxWidth: '1000px',
        maxHeight: '80%',
        bgcolor: 'background.paper',
        border: 2,
        borderColor: 'primary.main',
        borderRadius: 2,
        p: 2,
        zIndex: 1300,
        overflow: 'auto',
      }}
    >
      {/* Header */}
      <Box sx={{ mb: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="h6" sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <CodeIcon color="primary" />
            Code Changes Preview
          </Typography>
          <Button
            variant="outlined"
            size="small"
            onClick={onClose}
            startIcon={<RejectIcon />}
          >
            Close
          </Button>
        </Box>

        {description && (
          <Alert severity="info" sx={{ mb: 2 }}>
            <Typography variant="body2">{displayDescription}</Typography>
          </Alert>
        )}

        {/* Multi-file summary */}
        {isMultiFile && multiFileStats && (
          <Alert severity="info" sx={{ mb: 2 }}>
            <Typography variant="body2">
              {multiFileStats.totalFiles} file(s) with changes • {multiFileStats.totalChanges} total line changes
            </Typography>
          </Alert>
        )}

        {/* Diff Statistics */}
        <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
          <Chip
            label={`${diffStats.originalLength} → ${diffStats.modifiedLength} lines`}
            size="small"
            variant="outlined"
            color="info"
          />
          {diffStats.linesAdded > 0 && (
            <Chip
              label={`+${diffStats.linesAdded} lines added`}
              size="small"
              variant="outlined"
              color="success"
            />
          )}
          {diffStats.linesChanged > 0 && (
            <Chip
              label={`${diffStats.linesChanged} lines modified`}
              size="small"
              variant="outlined"
              color="warning"
            />
          )}
        </Stack>
      </Box>

      {/* Tab Navigation */}
      <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}>
        {isMultiFile ? (
          <Tabs value={activeTab} onChange={handleTabChange} variant="scrollable" scrollButtons="auto">
            {files.map((file, index) => (
              <Tab
                key={index}
                label={file.filePath || `File ${index + 1}`}
                icon={<CodeIcon />}
                iconPosition="start"
                sx={{ fontSize: '0.8rem' }}
              />
            ))}
          </Tabs>
        ) : (
        <Tabs value={activeTab} onChange={handleTabChange}>
          <Tab
            label="Original"
            icon={<CodeIcon />}
            iconPosition="start"
            sx={{ fontSize: '0.8rem' }}
          />
          <Tab
            label="Modified"
            icon={<PreviewIcon />}
            iconPosition="start"
            sx={{ fontSize: '0.8rem' }}
          />
          <Tab
            label="Side-by-Side"
            icon={<DiffIcon />}
            iconPosition="start"
            sx={{ fontSize: '0.8rem' }}
          />
        </Tabs>
        )}
      </Box>

      {/* Content */}
      <Box sx={{ mb: 3 }}>
        {isMultiFile ? (
          // Multi-file mode: show current file diff
          <>
            {activeTab === 0 && renderEditor(displayOriginalCode, `Original: ${displayFilePath || 'File'}`)}
            {activeTab === 1 && renderEditor(displayModifiedCode, `Modified: ${displayFilePath || 'File'}`)}
            {activeTab === 2 && renderDiffView()}
          </>
        ) : (
          // Single-file mode: show original, modified, or side-by-side
          <>
            {activeTab === 0 && renderEditor(displayOriginalCode, 'Original Code')}
            {activeTab === 1 && renderEditor(displayModifiedCode, 'Modified Code')}
        {activeTab === 2 && renderDiffView()}
          </>
        )}
      </Box>

      {/* Action Buttons */}
      <Box sx={{ display: 'flex', justifyContent: 'center', gap: 2 }}>
        <ButtonGroup variant="contained" size="large">
          <Button
            onClick={onAccept}
            color="success"
            startIcon={<AcceptIcon />}
            sx={{ px: 4 }}
          >
            Accept Changes
          </Button>
          <Button
            onClick={onReject}
            color="error"
            startIcon={<RejectIcon />}
            sx={{ px: 4 }}
          >
            Reject Changes
          </Button>
        </ButtonGroup>
      </Box>
    </Paper>
  );
};

export default CodeDiffViewer;