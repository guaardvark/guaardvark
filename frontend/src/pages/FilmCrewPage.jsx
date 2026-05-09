import React, { useState, useEffect, useCallback, useRef } from 'react';
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
  regenerateShot
} from '../api/productionService';

const FilmCrewPage = () => {
  const [tab, setTab] = useState(0);
  const [productions, setProductions] = useState([]);
  const [selectedProdId, setSelectedProdId] = useState(null);
  const [productionDetail, setProductionDetail] = useState(null);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState(null);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);

  // Tracks the last requested production id so a slow detail-fetch can't
  // overwrite a faster newer one. Without this, clicking A then B while A
  // is still in flight could land A's response *after* B and stick the wrong
  // detail panel up.
  const detailRequestId = useRef(0);

  const fetchProductions = useCallback(async () => {
    try {
      const data = await listProductions();
      setProductions(data.productions || []);
    } catch (err) {
      setError('Failed to load productions');
    }
  }, []);

  const fetchDetail = useCallback(async (id) => {
    if (!id) return;
    detailRequestId.current += 1;
    const myRequestId = detailRequestId.current;
    setLoadingDetail(true);
    try {
      const data = await getProduction(id);
      // Drop the response if a newer fetch was kicked off while we were
      // waiting — the user has moved on.
      if (myRequestId !== detailRequestId.current) return;
      setProductionDetail(data);
    } catch (err) {
      if (myRequestId === detailRequestId.current) {
        setError('Failed to fetch production details');
      }
    } finally {
      if (myRequestId === detailRequestId.current) {
        setLoadingDetail(false);
      }
    }
  }, []);

  useEffect(() => {
    setLoadingList(true);
    fetchProductions().finally(() => setLoadingList(false));
  }, [fetchProductions]);

  // Polling for active productions
  useEffect(() => {
    const active = productionDetail && !['complete', 'failed'].includes(productionDetail.status) && 
                   !['casting', 'awaiting_approval'].includes(productionDetail.current_stage);
    
    let interval;
    if (active) {
      interval = setInterval(() => {
        fetchProductions();
        fetchDetail(selectedProdId);
      }, 5000);
    }
    return () => clearInterval(interval);
  }, [productionDetail, selectedProdId, fetchProductions, fetchDetail]);

  const handleProductionSelect = (id) => {
    setSelectedProdId(id);
    fetchDetail(id);
    setTab(0); // Switch to Productions tab if we were in Cast Library
  };

  const handleCreateProduction = async (data) => {
    const newProd = await createProduction(data);
    await fetchProductions();
    handleProductionSelect(newProd.id);
  };

  const handleApprove = async () => {
    if (!selectedProdId) return;
    setApproving(true);
    setError(null);
    try {
      await approveStoryboard(selectedProdId);
      await fetchDetail(selectedProdId);
      await fetchProductions();
    } catch (err) {
      setError('Failed to approve storyboard');
    } finally {
      setApproving(false);
    }
  };

  const handleRegen = async (shotId, data) => {
    if (!selectedProdId) return;
    await regenerateShot(selectedProdId, shotId, data);
    await fetchDetail(selectedProdId);
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh', bgcolor: 'background.default' }}>
      <AppBar position="static" color="default" elevation={1}>
        <Toolbar variant="dense">
          <Typography variant="h6" sx={{ flexGrow: 1, display: 'flex', alignItems: 'center' }}>
            <MovieFilterIcon sx={{ mr: 1 }} /> Film Crew
          </Typography>
          <Tabs value={tab} onChange={(_, v) => setTab(v)}>
            <Tab label="Productions" icon={<MovieFilterIcon />} iconPosition="start" />
            <Tab label="Cast Library" icon={<GroupIcon />} iconPosition="start" />
          </Tabs>
          <Box sx={{ ml: 2 }}>
            <Button 
              variant="contained" 
              startIcon={<AddIcon />} 
              onClick={() => setCreateDialogOpen(true)}
              size="small"
            >
              New Production
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
                approving={approving}
                onCastingConfirmed={() => fetchDetail(selectedProdId)}
                onRegenerateShot={handleRegen}
                onApproveStoryboard={handleApprove}
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
