/**
 * sw.js — Pi extension service worker (T-190).
 * Handles: action click (open side panel) + context menu.
 */

const BRAIN_URL = "http://127.0.0.1:7712";
const MENU_ID = "pi-ask-page";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU_ID,
    title: "Ask Pi about this",
    contexts: ["selection", "page"],
  });
  // Enable side panel on all pages
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== MENU_ID) return;
  // Store context so side panel can pick it up
  chrome.storage.session.set({
    pi_page_context: {
      selection: info.selectionText || "",
      url: info.pageUrl || tab?.url || "",
      title: tab?.title || "",
    }
  });
  chrome.sidePanel.open({ tabId: tab.id });
});
