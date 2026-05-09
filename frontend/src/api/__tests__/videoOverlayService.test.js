import { describe, it, expect, vi } from 'vitest';
import axios from 'axios';
import { renderTimeline, getRenderStatus } from '../videoOverlayService';

vi.mock('axios');

describe('videoOverlayService', () => {
  it('renderTimeline returns job_id from 202 response', async () => {
    const mockResponse = {
      data: {
        data: { job_id: 'test-job-123', status: 'pending' },
        message: 'Render dispatched',
        status: 202
      }
    };
    axios.post.mockResolvedValueOnce(mockResponse);

    const result = await renderTimeline({ video_document_id: 1 });
    
    expect(axios.post).toHaveBeenCalledWith(
      expect.stringContaining('/video-overlay/render-timeline'),
      { video_document_id: 1 },
      { timeout: 30000 }
    );
    expect(result).toEqual({ job_id: 'test-job-123', status: 'pending' });
  });

  it('getRenderStatus hits the right URL and returns the status payload', async () => {
    const mockResponse = {
      data: {
        data: { job_id: 'test-job-123', status: 'running', progress: 50 },
        message: 'Success',
        status: 200
      }
    };
    axios.get.mockResolvedValueOnce(mockResponse);

    const result = await getRenderStatus('test-job-123');
    
    expect(axios.get).toHaveBeenCalledWith(
      expect.stringContaining('/video-overlay/render-status/test-job-123')
    );
    expect(result).toEqual({ job_id: 'test-job-123', status: 'running', progress: 50 });
  });
});
