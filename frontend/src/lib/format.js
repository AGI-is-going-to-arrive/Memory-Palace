import i18n, { DEFAULT_LOCALE } from '../i18n';

const resolveLocale = (lng) => lng || i18n.resolvedLanguage || i18n.language || DEFAULT_LOCALE;

const parseDate = (value) => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

export const formatDateTime = (value, lng, options = {}) => {
  const parsed = parseDate(value);
  if (!parsed) return null;
  return new Intl.DateTimeFormat(resolveLocale(lng), options).format(parsed);
};

export const formatTime = (value, lng, options = {}) => {
  const parsed = parseDate(value);
  if (!parsed) return null;
  return new Intl.DateTimeFormat(resolveLocale(lng), options).format(parsed);
};

export const formatNumber = (value, lng, options = {}) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return null;
  }
  return new Intl.NumberFormat(resolveLocale(lng), options).format(Number(value));
};
