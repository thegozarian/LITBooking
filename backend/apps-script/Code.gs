/**
 * Read-only integration probe for the Shopify App Proxy -> Apps Script path.
 * This endpoint intentionally does not create holds or expose request data.
 * Remove or replace it before implementing booking operations.
 */
function doGet(e) {
  return probeResponse_("GET", e);
}

function doPost(e) {
  return probeResponse_("POST", e);
}

function probeResponse_(method, e) {
  var params = e && e.parameters ? e.parameters : {};
  var queryKeys = Object.keys(params).sort();
  var postData = e && e.postData ? e.postData : null;

  var result = {
    probe: "shopify-app-proxy",
    version: 1,
    method: method,
    receivedAt: new Date().toISOString(),
    hasShop: Boolean(e && e.parameter && e.parameter.shop),
    hasProxySignature: Boolean(e && e.parameter && e.parameter.signature),
    hasPathPrefix: Boolean(e && e.parameter && e.parameter.path_prefix),
    queryKeys: queryKeys,
    bodyPresent: Boolean(postData && postData.contents),
    bodyLength: postData && postData.contents ? postData.contents.length : 0,
    contentType: postData && postData.type ? postData.type : null
  };

  return ContentService
    .createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}
