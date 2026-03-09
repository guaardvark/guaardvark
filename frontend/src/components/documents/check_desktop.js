import { firefox } from 'playwright';

(async () => {
  const browser = await firefox.launch();
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await context.newPage();
  
  // Listen to console messages
  page.on('console', msg => console.log('BROWSER:', msg.text()));
  
  try {
    await page.goto('http://localhost:5174/documents', { waitUntil: 'networkidle' });
    await page.waitForTimeout(3000);
    
    // Check if folders/files are displayed
    const gridItems = await page.locator('.react-grid-item').count();
    console.log(`Found ${gridItems} grid items`);
    
    // Check for any error messages
    const errors = await page.locator('[role="alert"]').count();
    console.log(`Found ${errors} error alerts`);
    
    await page.screenshot({ path: '/tmp/desktop_check.png', fullPage: true });
    console.log('Screenshot saved');
    
  } catch (error) {
    console.error('Error:', error.message);
  } finally {
    await browser.close();
  }
})();
