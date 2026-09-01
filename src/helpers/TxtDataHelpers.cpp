#include "TxtDataHelpers.h"
#include <math.h>
#include <string.h>

void StrHelper::strncpy(char* dest, const char* src, size_t buf_sz) {
  while (buf_sz > 1 && *src) {
    *dest++ = *src++;
    buf_sz--;
  }
  *dest = 0;  // truncates if needed
}

void StrHelper::strzcpy(char* dest, const char* src, size_t buf_sz) {
  while (buf_sz > 1 && *src) {
    *dest++ = *src++;
    buf_sz--;
  }
  while (buf_sz > 0) {  // pad remaining with nulls
    *dest++ = 0;
    buf_sz--;
  }
}

void StrHelper::stripSurroundingQuotes(char* str, size_t buf_sz) {
  if (!str || buf_sz == 0) return;
  size_t len = strlen(str);
  if (len == 0) return;
  if (str[0] == '"' || str[0] == '\'') {
    memmove(str, str + 1, len);
    len--;
  }
  if (len > 0 && (str[len - 1] == '"' || str[len - 1] == '\'')) {
    str[len - 1] = '\0';
  }
}

bool StrHelper::isBlank(const char* str) {
  while (*str) {
    if (*str++ != ' ') return false;
  }
  return true;
}

#include <Arduino.h>

union int32_Float_t 
{
  int32_t Long;
  float Float;
};

#ifndef FLT_MIN_EXP
#define FLT_MIN_EXP (-999)
#endif
 
#ifndef FLT_MAX_EXP
#define FLT_MAX_EXP (999)
#endif
 
#define _FTOA_TOO_LARGE -2  // |input| > 2147483520 
#define _FTOA_TOO_SMALL -1  // |input| < 0.0000001 
 
//precision 0-9
#define PRECISION 7
 
//_ftoa function 
static void _ftoa(float f, char *p, int *status) 
{
  int32_t mantissa, int_part, frac_part;
  int16_t exp2;
  int32_Float_t x;
 
  *status = 0;
  if (f == 0.0) 
  {
    *p++ = '0';
    *p++ = '.';
    *p++ = '0';
    *p = 0;
    return;
  }
 
  x.Float = f;
  exp2 = (unsigned char)(x.Long>>23) - 127;
  mantissa = (x.Long&0xFFFFFF) | 0x800000;
  frac_part = 0;
  int_part = 0;
 
  if (exp2 >= 31) 
  {
    *status = _FTOA_TOO_LARGE;
    return;
  } 
  else if (exp2 < -23) 
  {
    *status = _FTOA_TOO_SMALL;
    return;
  } 
  else if (exp2 >= 23)
  { 
    int_part = mantissa<<(exp2 - 23);
  }
  else if (exp2 >= 0) 
  {
    int_part = mantissa>>(23 - exp2);
    frac_part = (mantissa<<(exp2 + 1))&0xFFFFFF;
  } 
  else 
  {
    //if (exp2 < 0)
    frac_part = (mantissa&0xFFFFFF)>>-(exp2 + 1);
  }
 
  if (x.Long < 0)
      *p++ = '-';
  if (int_part == 0)
    *p++ = '0';
  else 
  {
    char tmp[16];
    snprintf(tmp, sizeof(tmp), "%ld", (long)int_part);
    strcpy(p, tmp);
    while (*p)
      p++;
  }
  *p++ = '.';
  if (frac_part == 0)
    *p++ = '0';
  else 
  {
    char m;
 
    for (m=0; m<PRECISION; m++) 
    {
      //frac_part *= 10;
      frac_part = (frac_part<<3) + (frac_part<<1); 
      *p++ = (frac_part>>24) + '0';
      frac_part &= 0xFFFFFF;
    }
 
    //delete ending zeroes
    for (--p; p[0] == '0' && p[-1] != '.'; --p)
      ;
    ++p;
  }
  *p = 0;
}

const char* StrHelper::ftoa(float f) {
  static char tmp[16];
  int status;
  _ftoa(f, tmp, &status);
  if (status) {
    tmp[0] = '0';  // fallback/error value
    tmp[1] = 0;
  }
  return tmp;
}

const char* StrHelper::ftoa3(float f) {
  static char s[16];
  int v = (int)(f * 1000.0f + (f >= 0 ? 0.5f : -0.5f)); // rounded ×1000
  int w = v / 1000;                                     // whole
  int d = abs(v % 1000);                                // decimals
  snprintf(s, sizeof(s), "%d.%03d", w, d);
  for (int i = strlen(s) - 1; i > 0 && s[i] == '0'; i--)
      s[i] = 0;
  int L = strlen(s);
  if (s[L - 1] == '.') s[L - 1] = 0;
  return s;
}

uint32_t StrHelper::fromHex(const char* src) {
  uint32_t n = 0;
  while (*src) {
    if (*src >= '0' && *src <= '9') {
      n <<= 4;
      n |= (*src - '0');
    } else if (*src >= 'A' && *src <= 'F') {
      n <<= 4;
      n |= (*src - 'A' + 10);
    } else if (*src >= 'a' && *src <= 'f') {
      n <<= 4;
      n |= (*src - 'a' + 10);
    } else {
      break;  // non-hex char encountered, stop parsing
    }
    src++;
  }
  return n;
}

static size_t boundedTextLen(const uint8_t* data, size_t len) {
  size_t n = 0;
  while (n < len && data[n] != 0) n++;
  return n;
}

bool StrHelper::isValidUTF8(const uint8_t* data, size_t len) {
  size_t i = 0;
  while (i < len) {
    uint8_t c = data[i++];
    if (c == 0) break;
    if (c < 0x80) continue;

    uint32_t codepoint;
    uint8_t needed;
    if ((c & 0xE0) == 0xC0) {
      codepoint = c & 0x1F;
      needed = 1;
      if (codepoint == 0) return false; // overlong ASCII
    } else if ((c & 0xF0) == 0xE0) {
      codepoint = c & 0x0F;
      needed = 2;
    } else if ((c & 0xF8) == 0xF0) {
      codepoint = c & 0x07;
      needed = 3;
    } else {
      return false;
    }

    if (i + needed > len) return false;
    for (uint8_t j = 0; j < needed; j++) {
      uint8_t cc = data[i++];
      if ((cc & 0xC0) != 0x80) return false;
      codepoint = (codepoint << 6) | (cc & 0x3F);
    }

    if (needed == 1 && codepoint < 0x80) return false;
    if (needed == 2 && codepoint < 0x0800) return false;
    if (needed == 3 && codepoint < 0x10000) return false;
    if (codepoint > 0x10FFFF) return false;
    if (codepoint >= 0xD800 && codepoint <= 0xDFFF) return false;
    if (codepoint >= 0xFDD0 && codepoint <= 0xFDEF) return false;
    if ((codepoint & 0xFFFE) == 0xFFFE) return false;
  }
  return true;
}

bool StrHelper::isTimestampSane(uint32_t timestamp, uint32_t now) {
  if (timestamp == 0) return false;
  if (timestamp < 1577836800UL) return false; // 2020-01-01, before MeshCore-era public chat.
  if (now >= 1577836800UL && timestamp > now + (2UL * 24UL * 60UL * 60UL)) return false;
  return true;
}

uint8_t StrHelper::messageConfidenceScore(const uint8_t* data, size_t len, uint32_t timestamp, uint32_t now,
                                          TextQualityMetrics* metrics) {
  TextQualityMetrics m;
  memset(&m, 0, sizeof(m));
  m.payload_len = len;
  m.text_len = boundedTextLen(data, len);
  m.valid_utf8 = isValidUTF8(data, m.text_len);
  m.timestamp_sane = isTimestampSane(timestamp, now);
  for (size_t i = m.text_len + 1; i < len; i++) {
    if (data[i] != 0 && m.trailing_nonzero < 0xFFFF) m.trailing_nonzero++;
  }

  uint8_t counts[256];
  memset(counts, 0, sizeof(counts));
  for (size_t i = 0; i < m.text_len; i++) {
    uint8_t c = data[i];
    if (counts[c] < 255) counts[c]++;
    if (c >= 0x20 && c <= 0x7E) {
      m.printable_ascii++;
      if (c == ' ') m.whitespace++;
    } else if (c == '\t' || c == '\n' || c == '\r') {
      m.whitespace++;
    } else if (c < 0x20 || c == 0x7F) {
      m.control_chars++;
    } else {
      m.non_ascii++;
    }
    if (c == 0xEF && i + 2 < m.text_len && data[i + 1] == 0xBF && data[i + 2] == 0xBD) {
      m.replacement_chars++;
    }
  }

  double entropy = 0.0;
  if (m.text_len > 0) {
    for (int i = 0; i < 256; i++) {
      if (counts[i] == 0) continue;
      double p = ((double)counts[i]) / ((double)m.text_len);
      entropy -= p * (log(p) / log(2.0));
    }
  }
  m.entropy_score = entropy >= 8.0 ? 100 : (uint8_t)((entropy * 100.0) / 8.0);

  int score = 100;
  if (m.text_len == 0) score -= 45;
  if (!m.valid_utf8) score -= 65;
  if (!m.timestamp_sane) score -= 15;
  if (m.trailing_nonzero > 0) score -= 35;

  if (m.text_len > 0) {
    uint32_t printable_ratio = ((uint32_t)m.printable_ascii * 100UL) / m.text_len;
    uint32_t whitespace_ratio = ((uint32_t)m.whitespace * 100UL) / m.text_len;
    uint32_t control_ratio = ((uint32_t)m.control_chars * 100UL) / m.text_len;
    uint32_t replacement_ratio = ((uint32_t)m.replacement_chars * 100UL) / m.text_len;

    if (printable_ratio < 50 && m.non_ascii < m.printable_ascii) score -= 25;
    if (whitespace_ratio > 45) score -= 15;
    if (control_ratio > 0) score -= (control_ratio > 10) ? 45 : 25;
    if (replacement_ratio > 0) score -= (replacement_ratio > 5) ? 45 : 25;
    if (m.entropy_score > 72 && printable_ratio < 75) score -= 25;
  }

  if (metrics) *metrics = m;
  if (score < 0) return 0;
  if (score > 100) return 100;
  return (uint8_t)score;
}

void StrHelper::sanitizeTextForDisplay(char* text) {
  char* start = text;
  char* dst = text;
  bool last_space = false;
  while (*text) {
    uint8_t c = (uint8_t)*text++;
    if (c < 0x20 || c == 0x7F) {
      if (!last_space) {
        *dst++ = ' ';
        last_space = true;
      }
      continue;
    }
    *dst++ = (char)c;
    last_space = (c == ' ');
  }
  while (dst > start && dst[-1] == ' ') dst--;
  *dst = 0;
}
