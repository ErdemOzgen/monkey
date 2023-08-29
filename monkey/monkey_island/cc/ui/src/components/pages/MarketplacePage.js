import React, {useContext, useState} from 'react';
import Tabs from '@mui/material/Tabs';
import {Tab, Box, Badge} from '@mui/material';
import {PluginsContext} from '../contexts/plugins/PluginsContext';
import AvailablePlugins from '../ui-components/plugins-marketplace/AvailablePlugins';
import classes from '../../styles/pages/Marketplace.module.scss';
import UploadNewPlugin from '../ui-components/plugins-marketplace/UploadNewPlugin';
import AuthComponent from '../AuthComponent';

const TabPanel = (props) => {
  const {children, value, index, ...other} = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`full-width-tabpanel-${index}`}
      aria-labelledby={`full-width-tab-${index}`}
      {...other}
    >
      {value === index && (
        <Box sx={{px: 1, py: 3}}>
          {children}
        </Box>
      )}
    </div>
  );
}

const a11yProps = (index) => {
  return {
    id: `full-width-tab-${index}`,
    'aria-controls': `full-width-tabpanel-${index}`
  };
}

const MarketplacePage = () => {
  const {numberOfPluginsThatRequiresUpdate} = useContext(PluginsContext);

  const [tabValue, setTabValue] = useState(0);

  const handleChange = (_event, newValue) => {
    setTabValue(newValue);
  };

  const installedPluginsLabel = <div>
    <Badge badgeContent={numberOfPluginsThatRequiresUpdate} color="error">
      <span id="installed-plugins-tab-label">Installed Plugins</span>
    </Badge>
  </div>

  return (
    <PluginsContext.Provider value={{availablePlugins, installedPlugins, UploadNewPlugin, refreshAvailablePlugins, refreshInstalledPlugins}}>
      <Box className="main col-xl-8 col-lg-8 col-md-9 col-sm-9 offset-xl-2 offset-lg-3 offset-md-3 offset-sm-3">
        <h1 className='page-title'>Plugins</h1>
        <Box sx={{borderBottom: 1, borderColor: 'divider'}}>
          <Tabs value={tabValue}
                onChange={handleChange}
                indicatorColor="secondary"
                textColor="inherit"
                variant="fullWidth"
                aria-label="full width tabs">
            <Tab label="Available Plugins" {...a11yProps(0)}/>
            <Tab label="Installed Plugins" {...a11yProps(1)}/>
            <Tab label="Upload New Plugin" {...a11yProps(2)}/>
          </Tabs>
        </Box>
        <TabPanel value={tabValue} index={0}><AvailablePlugins /></TabPanel>
        <TabPanel value={tabValue} index={1}>Installed Plugins</TabPanel>
        <TabPanel value={tabValue} index={2}><UploadNewPlugin/></TabPanel>
      </Box>
  )
};

export default MarketplacePage;
