import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import useSlashCommands from './useSlashCommands';
import { useAppStore } from '../stores/useAppStore';

function jsonResponse(data) {
  return {
    ok: true,
    json: async () => data,
  };
}

function mockFetch() {
  global.fetch.mockImplementation(async (url, options = {}) => {
    const path = String(url);

    if (path.startsWith('/api/rules')) {
      return jsonResponse({ data: { rules: [] } });
    }

    if (path.includes('/api/chat-sessions/') && path.endsWith('/mode')) {
      const body = options.body ? JSON.parse(options.body) : {};
      return jsonResponse({
        success: true,
        session_id: 'session_1',
        mode: body.mode || 'chat',
      });
    }

    if (path.includes('/api/chat/unified/') && path.endsWith('/abort')) {
      return jsonResponse({ success: true });
    }

    if (path === '/api/agent-control/kill') {
      return jsonResponse({ success: true });
    }

    return jsonResponse({ success: true });
  });
}

function renderSlashHook(overrides = {}) {
  const addMessage = vi.fn();
  const updateMessage = vi.fn();
  const onSendMessage = vi.fn();
  const setInputText = vi.fn();

  const hook = renderHook(() =>
    useSlashCommands({
      addMessage,
      updateMessage,
      onSendMessage,
      setInputText,
      chatState: {
        sessionId: 'session_1',
        projectId: null,
        clearMessages: vi.fn(),
        onPlanCreated: vi.fn(),
      },
      ...overrides,
    })
  );

  return { ...hook, addMessage, updateMessage, onSendMessage, setInputText };
}

describe('useSlashCommands mode switching', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch();
    useAppStore.setState({ sessionModes: {} });
  });

  it('executes exact optional /agent on Enter instead of inserting autocomplete text', async () => {
    const { result, addMessage, setInputText } = renderSlashHook();
    const event = {
      key: 'Enter',
      preventDefault: vi.fn(),
      stopPropagation: vi.fn(),
    };

    act(() => {
      result.current.handleInputChange('/agent');
    });

    await waitFor(() => {
      expect(result.current.popupVisible).toBe(true);
    });

    act(() => {
      result.current.handleKeyDown(event);
    });

    await waitFor(() => {
      expect(useAppStore.getState().getSessionMode('session_1')).toBe('agent');
    });

    expect(event.preventDefault).toHaveBeenCalled();
    expect(setInputText).toHaveBeenCalledWith('');
    expect(setInputText).not.toHaveBeenCalledWith('/agent ');
    expect(addMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        role: 'system',
        content: expect.stringContaining('agent mode'),
      })
    );
  });

  it('switches to agent mode and sends an immediate /agent task', async () => {
    const { result, onSendMessage } = renderSlashHook();

    await act(async () => {
      await result.current.executeCommand('/agent click the button');
    });

    expect(useAppStore.getState().getSessionMode('session_1')).toBe('agent');
    expect(onSendMessage).toHaveBeenCalledWith('click the button', null);
  });

  it('always PATCHes /chat even when local cache already says chat', async () => {
    useAppStore.getState().setSessionMode('session_1', 'chat');
    const { result, addMessage } = renderSlashHook();

    await act(async () => {
      await result.current.executeCommand('/chat');
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/chat-sessions/session_1/mode',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ mode: 'chat' }),
      })
    );
    expect(addMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        role: 'system',
        content: 'Already in chat mode.',
      })
    );
  });
});
