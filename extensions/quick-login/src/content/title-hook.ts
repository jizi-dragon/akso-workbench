import { CONTENT_MESSAGE } from '../shared/constants';

let alias = '';

function apply(): void {
  if (alias && document.title !== alias) {
    document.title = alias;
  }
}

function startTitleHook(): void {
  const observer = new MutationObserver(() => apply());
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
  });
  apply();
}

chrome.runtime.onMessage.addListener((msg: unknown) => {
  if (
    msg &&
    typeof msg === 'object' &&
    (msg as { type?: string }).type === CONTENT_MESSAGE.setTitle
  ) {
    alias = (msg as { alias?: string }).alias ?? '';
    apply();
  }
});

startTitleHook();
