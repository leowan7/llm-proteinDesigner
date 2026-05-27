import { useEffect, useState, type ReactNode } from "react";
import {
  readConsent,
  writeConsent,
  COOKIE_CONSENT_EVENT,
} from "@/lib/cookieConsent";
import { CookieConsentBanner } from "./CookieConsentBanner";

interface Props {
  children: ReactNode;
}

/**
 * Mounts the cookie consent banner as a sibling to the app tree.
 *
 * Behavior:
 * - On first visit (no valid record in localStorage), the banner is visible.
 * - Clicking "Got it" persists the record and hides the banner.
 * - Any component can dispatch `bindwave:open-cookie-consent` on `window`
 *   (e.g. via `requestOpenConsent()` from @/lib/cookieConsent) to re-open
 *   the banner for review, even after the user has previously accepted.
 *
 * Must be rendered INSIDE BrowserRouter because the banner uses react-router
 * Link to reach /legal/cookies.
 */
export function CookieConsentProvider({ children }: Props) {
  const [visible, setVisible] = useState<boolean>(() => readConsent() === null);

  useEffect(() => {
    const onOpen = () => setVisible(true);
    window.addEventListener(COOKIE_CONSENT_EVENT, onOpen);
    return () => window.removeEventListener(COOKIE_CONSENT_EVENT, onOpen);
  }, []);

  function handleAccept() {
    writeConsent();
    setVisible(false);
  }

  return (
    <>
      {children}
      {visible && <CookieConsentBanner onAccept={handleAccept} />}
    </>
  );
}
