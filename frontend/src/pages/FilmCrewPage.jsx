import React, { useState, useEffect, useCallback, useRef } from 'react';
import { formatUiError } from "../utils/uiError";
import { dispatchWarning } from "../api/taskQueue";
import {
  Box,
  Typography,
  Button,
  Tabs,
  Tab,
  AppBar,
  Toolbar,
  Container
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import MovieFilterIcon from '@mui/icons-material/MovieFilter';
import GroupIcon from '@mui/icons-material/Group';

import ProductionList from '../components/filmcrew/ProductionList';
import ProductionDetail from '../components/filmcrew/ProductionDetail';
import CreateProductionDialog from '../components/filmcrew/CreateProductionDialog';
import CastLibraryView from '../components/filmcrew/CastLibraryView';

import { 
  listProductions, 
  getProduction, 
  createProduction,
  approveStoryboard,
  regenerateShot,
  deleteProduction,
  retryProduction,
} from '../api/productionService';
import { useUiTranslation } from '../i18n';

const FilmCrewPage = () => {
  const { t } = useUiTranslation();
  const [tab, setTab] = useState(0);
  const [productions, setProductions] = useState([]);
  const [selectedProdId, setSelectedProdId] = useState(null);
  const [productionDetail, setProductionDetail] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [approving, setApproving] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState(null);
  // A step the backend saved but could not queue ({dispatched: false,
  // warning}); shown on the production it belongs to.
  const [notice, setNotice] = useState(null);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [regenPolling, setRegenPolling] = useState(false);

  // Tracks the last requested production id so a slow detail-fetch can't
  // overwrite a faster newer one. Without this, clicking A then B while A
  // is still in flight could land A's response *after* B and stick the wrong
  // detail panel up.
  const detailRequestId = useRef(0);
  const regenPollCount = useRef(0);

  const fetchProductions = useCallback(async () => {
    try {
      const data = await listProductions();
      setProductions(data.productions || []);
      setError(null);
    } catch (err) {
      setError(t('filmCrew.page.loadFailed'));
    }
  }, []);

  // `quiet` refreshes the production already on screen without the loading
  // spinner. The spinner replaces the whole detail view, which unmounts the
  // storyboard grid and with it an open "Regenerate shot" dialog and the
  // prompt being typed there. The 5 s poll and the refresh after a shot regen
  // (both run while the person may be typing the next override) stay quiet.
  const fetchDetail = useCallback(async (id, { quiet = false } = {}) => {
    if (!id) return;
    detailRequestId.current += 1;
    const myRequestId = detailRequestId.current;
    if (!quiet) setLoadingDetail(true);
    try {
      const data = await getProduction(id);
      // Drop the response if a newer fetch was kicked off while we were
      // waiting — the user has moved on.
      if (myRequestId !== detailRequestId.current) return;
      setProductionDetail(data);
      setError(null);
    } catch (err) {
      if (myRequestId === detailRequestId.current) {
        setError(t('filmCrew.page.detailFailed'));
      }
    } finally {
      if (myRequestId === detailRequestId.current) {
        setLoadingDetail(false);
      }
    }
  }, []);

  useEffect(() => {
    fetchProductions();
  }, [fetchProductions]);

  // Polling for active productions
  useEffect(() => {
    const isFailed = productionDetail?.status?.startsWith('failed');
    const terminal = ['complete', 'failed'].includes(productionDetail?.status) || isFailed;
    const active = productionDetail && !terminal &&
                   productionDetail.current_stage !== 'casting' &&
                   (productionDetail.current_stage !== 'awaiting_approval' || regenPolling);
    
    let interval;
    if (active) {
      interval = setInterval(async () => {
        await fetchProductions();
        await fetchDetail(selectedProdId, { quiet: true });
        if (productionDetail.current_stage === 'awaiting_approval' && regenPolling) {
          regenPollCount.current += 1;
          if (regenPollCount.current >= 12) {
            setRegenPolling(false);
          }
        }
      }, 5000);
    }
    return () => clearInterval(interval);
  }, [productionDetail, selectedProdId, fetchProductions, fetchDetail, regenPolling]);

  const handleProductionSelect = (id) => {
    setError(null);
    setRegenPolling(false);
    regenPollCount.current = 0;
    setSelectedProdId(id);
    fetchDetail(id);
    setTab(0); // Switch to Productions tab if we were in Cast Library
  };

  const noteDispatch = (id, result) => {
    const warning = dispatchWarning(result);
    if (warning) setNotice({ id, text: warning });
  };

  const handleCreateProduction = async (data) => {
    const newProd = await createProduction(data);
    await fetchProductions();
    handleProductionSelect(newProd.id);
    noteDispatch(newProd.id, newProd);
  };

  const handleApprove = async (options) => {
    if (!selectedProdId) return;
    setApproving(true);
    setError(null);
    try {
      const result = await approveStoryboard(selectedProdId, {
        confirmFlagged: options?.confirmFlagged === true,
      });
      noteDispatch(selectedProdId, result);
      await fetchDetail(selectedProdId);
      await fetchProductions();
    } catch (err) {
      const flagged = err?.response?.data?.flagged_shots;
      if (Array.isArray(flagged) && flagged.length) {
        // The curator flagged shots this page had not shown yet: reload the
        // cards so the next click asks about them.
        await fetchDetail(selectedProdId, { quiet: true });
        setError(t('filmCrew.page.flaggedOnApprove', { count: flagged.length }));
      } else {
        setError(formatUiError(err?.response?.data?.error) || t('filmCrew.page.approveFailed'));
      }
    } finally {
      setApproving(false);
    }
  };

  const handleRegen = async (shotId, data) => {
    if (!selectedProdId) return;
    setError(null);
    const result = await regenerateShot(selectedProdId, shotId, data);
    noteDispatch(selectedProdId, result);
    await fetchDetail(selectedProdId, { quiet: true });
    if (result?.regen_job_id) {
      regenPollCount.current = 0;
      setRegenPolling(true);
    }
    return result;
  };

  const handleRetry = async (id) => {
    setRetrying(true);
    setError(null);
    try {
      const result = await retryProduction(id);
      noteDispatch(id, result);
      await fetchDetail(id);
      await fetchProductions();
    } catch (err) {
      setError(formatUiError(err?.response?.data?.error) || t('filmCrew.page.retryFailed'));
    } finally {
      setRetrying(false);
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm(t('filmCrew.page.deleteConfirm'))) {
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteProduction(id);
      if (selectedProdId === id) {
        setSelectedProdId(null);
        setProductionDetail(null);
      }
      await fetchProductions();
    } catch (err) {
      setError(formatUiError(err?.response?.data?.error) || t('filmCrew.page.deleteFailed'));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh', bgcolor: 'background.default' }}>
      <AppBar position="static" color="default" elevation={1}>
        <Toolbar variant="dense">
          <Typography variant="h6" sx={{ flexGrow: 1, display: 'flex', alignItems: 'center' }}>
            <MovieFilterIcon sx={{ mr: 1 }} /> {t('filmCrew.page.title')}
          </Typography>
          <Tabs value={tab} onChange={(_, v) => setTab(v)}>
            <Tab label={t('filmCrew.page.productionsTab')} icon={<MovieFilterIcon />} iconPosition="start" />
            <Tab label={t('filmCrew.page.castLibraryTab')} icon={<GroupIcon />} iconPosition="start" />
          </Tabs>
          <Box sx={{ ml: 2 }}>
            <Button 
              variant="contained" 
              startIcon={<AddIcon />} 
              onClick={() => setCreateDialogOpen(true)}
              size="small"
            >
              {t('filmCrew.page.newProduction')}
            </Button>
          </Box>
        </Toolbar>
      </AppBar>

      <Box sx={{ flexGrow: 1, display: 'flex', overflow: 'hidden' }}>
        {tab === 0 ? (
          <>
            <Box sx={{ width: 300, flexShrink: 0 }}>
              <ProductionList 
                productions={productions} 
                selectedId={selectedProdId} 
                onSelect={handleProductionSelect}
              />
            </Box>
            <Box sx={{ flexGrow: 1, overflowY: 'auto' }}>
              <ProductionDetail
                production={productionDetail}
                loading={loadingDetail}
                error={error}
                notice={notice && notice.id === selectedProdId ? notice.text : null}
                onDismissNotice={() => setNotice(null)}
                approving={approving}
                retrying={retrying}
                deleting={deleting}
                onCastingConfirmed={(warning) => {
                  if (warning) setNotice({ id: selectedProdId, text: warning });
                  return fetchDetail(selectedProdId);
                }}
                onRegenerateShot={handleRegen}
                onApproveStoryboard={handleApprove}
                onRetry={handleRetry}
                onDelete={handleDelete}
              />
            </Box>
          </>
        ) : (
          <Container maxWidth="lg" sx={{ py: 3 }}>
            <CastLibraryView />
          </Container>
        )}
      </Box>

      <CreateProductionDialog 
        open={createDialogOpen}
        onClose={() => setCreateDialogOpen(false)}
        onCreated={handleCreateProduction}
      />
    </Box>
  );
};

export default FilmCrewPage;
