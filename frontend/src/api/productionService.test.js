import { describe, it, expect, vi, beforeEach } from 'vitest';
import axios from 'axios';
import { 
  listProductions, 
  getProduction, 
  createProduction,
  approveStoryboard,
  regenerateShot
} from './productionService';

vi.mock('axios');

describe('productionService', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('listProductions calls GET /api/production', async () => {
    axios.get.mockResolvedValue({ data: { productions: [] } });
    const result = await listProductions();
    expect(axios.get).toHaveBeenCalledWith(expect.stringContaining('/production'));
    expect(result.productions).toEqual([]);
  });

  it('getProduction calls GET /api/production/:id', async () => {
    axios.get.mockResolvedValue({ data: { id: 1 } });
    const result = await getProduction(1);
    expect(axios.get).toHaveBeenCalledWith(expect.stringContaining('/production/1'));
    expect(result.id).toBe(1);
  });

  it('createProduction calls POST /api/production', async () => {
    const payload = { name: 'Test', script_text: 'Text' };
    axios.post.mockResolvedValue({ data: { id: 1 } });
    const result = await createProduction(payload);
    expect(axios.post).toHaveBeenCalledWith(expect.stringContaining('/production'), payload);
    expect(result.id).toBe(1);
  });

  it('approveStoryboard calls POST /api/production/:id/storyboard/approve', async () => {
    axios.post.mockResolvedValue({ data: { success: true } });
    await approveStoryboard(1);
    expect(axios.post).toHaveBeenCalledWith(expect.stringContaining('/production/1/storyboard/approve'));
  });

  it('regenerateShot calls POST /api/production/:id/storyboard/shot/:shotId/regenerate', async () => {
    const payload = { prompt_override: 'new prompt' };
    axios.post.mockResolvedValue({ data: { success: true } });
    await regenerateShot(1, 10, payload);
    expect(axios.post).toHaveBeenCalledWith(
      expect.stringContaining('/production/1/storyboard/shot/10/regenerate'),
      payload
    );
  });
});
