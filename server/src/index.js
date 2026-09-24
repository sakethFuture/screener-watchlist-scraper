require('dotenv').config();
const { createApp } = require('./app');

const app = createApp();
const port = process.env.PORT || 3000;

app.listen(port, () => {
  console.log(`Fund Analysis API listening on :${port}`);
});
