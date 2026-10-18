"use client";

import { useSyncExternalStore } from "react";

/** A non-interactive decorative divider; it is deliberately not an entry button. */
export const FOOTER_DIVIDER_CODE = "-....--..-...- -....--..-...- -....--..-..-- -....--..-..-- -....--..-.... -....--..-..-. -....--..-.... -....--..-..-. -... .-";
export const FOOTER_DIVIDER_CODE_MOBILE = "-..----.------- ---.-.-..-.-... --.-....-..--.. -..-.---.--...-. ----.-.---.---- --...-..-.-..-- -.-----........ -------.-.-...- -..--....---.-.-";

const MOBILE_USER_AGENT = /Android|webOS|iPhone|iPod|BlackBerry|IEMobile|Opera Mini|Mobile/i;
const MOBILE_MEDIA_QUERY = "(max-width: 520px) and (pointer: coarse)";

export function isMobileUserAgent(userAgent: string) {
  return MOBILE_USER_AGENT.test(userAgent);
}

function isMobileDevice() {
  if (typeof window === "undefined") {
    return false;
  }

  return isMobileUserAgent(window.navigator.userAgent) || window.matchMedia(MOBILE_MEDIA_QUERY).matches;
}

function subscribe(onStoreChange: () => void) {
  const mediaQuery = window.matchMedia(MOBILE_MEDIA_QUERY);
  mediaQuery.addEventListener("change", onStoreChange);
  return () => mediaQuery.removeEventListener("change", onStoreChange);
}

function getServerSnapshot() {
  return false;
}

function renderCode(code: string) {
  return code.split(" ").map((group, index, groups) => <span key={index}>{group}{index < groups.length - 1 ? " " : ""}</span>);
}

export function FooterCodeDivider() {
  const isMobile = useSyncExternalStore(subscribe, isMobileDevice, getServerSnapshot);
  return <div className="footer-code-rule" aria-hidden="true">{renderCode(isMobile ? FOOTER_DIVIDER_CODE_MOBILE : FOOTER_DIVIDER_CODE)}</div>;
}
