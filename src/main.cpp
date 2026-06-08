#include <Arduino.h>
#include <U8g2lib.h>
#include "images.h"
#include "esp_log.h"

// ============================================================
//  display-ctl — serial-controlled display for the ESP32-C3
//  Line-based command protocol. See PROTOCOL.md.
// ============================================================

#define FW_VERSION "1.0"

// ---- Pins ----
#define I2C_SDA     8
#define I2C_SCL     9
#define LED_PIN     3      // red LED (active high)
#define BUZZER_PIN  4      // buzzer (passive, driven via LEDC)
#define BUZZER_CH   0      // LEDC channel for the buzzer
#define OLED_ADDR   0x3C

// HW I2C at 400kHz; let U8g2 own the Wire bus (do NOT call Wire.begin()).
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE, I2C_SCL, I2C_SDA);

// ---- Fonts ----
enum FontId { F_SMALL, F_MED, F_BIG };
FontId curFont = F_SMALL;
static const uint8_t* fontOf(FontId f) {
  switch (f) {
    case F_BIG: return u8g2_font_10x20_tf;   // ~3 lines, ~12 cols
    case F_MED: return u8g2_font_7x13_tf;    // ~4 lines, ~18 cols
    default:    return u8g2_font_6x10_tf;    // ~6 lines, ~21 cols
  }
}

// ---- Buzzer / LED state ----
uint32_t buzzerOffAt = 0;                    // 0 = no scheduled stop
bool     ledBlink = false, ledState = false;
uint32_t ledPeriod = 500, ledLastToggle = 0;

// ---- Line buffer and parser ----
static char  lineBuf[260];
static size_t lineLen = 0;
static char* cur = nullptr;                  // parse cursor
static char* curId = nullptr;                // current command "@id", or nullptr

static char* nextToken() {
  while (*cur == ' ' || *cur == '\t') cur++;
  if (*cur == 0) return nullptr;
  char* start = cur;
  while (*cur && *cur != ' ' && *cur != '\t') cur++;
  if (*cur) { *cur = 0; cur++; }
  return start;
}
static char* restOfLine() {
  while (*cur == ' ' || *cur == '\t') cur++;
  return cur;                                // may be an empty string
}
static void upper(char* s) { for (; *s; s++) *s = toupper((unsigned char)*s); }

// ---- Responses (ACK) ----
static void ok()                 { if (curId) Serial.printf("%s OK\n", curId); else Serial.println("OK"); }
static void okMsg(const char* m) { if (curId) Serial.printf("%s OK %s\n", curId, m); else Serial.printf("OK %s\n", m); }
static void err(int code, const char* m) { if (curId) Serial.printf("%s ERR %d %s\n", curId, code, m); else Serial.printf("ERR %d %s\n", code, m); }
static void raw(const char* m)   { if (curId) Serial.printf("%s %s\n", curId, m); else Serial.println(m); }

static bool drawImageByName(const char* name) {
  const unsigned char* bits = nullptr;
  char n[16]; strncpy(n, name, sizeof(n) - 1); n[sizeof(n) - 1] = 0; upper(n);
  if      (!strcmp(n, "SPLASH"))  bits = img_splash;
  else if (!strcmp(n, "WARNING")) bits = img_warning;
  else if (!strcmp(n, "PANIC"))   bits = img_panic;
  if (!bits) return false;
  u8g2.clearBuffer();
  u8g2.drawXBM(0, 0, IMG_W, IMG_H, bits);
  u8g2.sendBuffer();                         // images render immediately
  return true;
}

static void processLine() {
  cur = lineBuf;
  while (*cur == ' ' || *cur == '\t') cur++;
  if (*cur == 0)   return;                    // empty line -> no response
  if (*cur == '#') return;                    // comment    -> no response

  curId = nullptr;
  if (*cur == '@') curId = nextToken();       // optional id, echoed in the response

  char* cmd = nextToken();
  if (!cmd) return;
  upper(cmd);

  if (!strcmp(cmd, "CLS")) {
    u8g2.clearBuffer(); ok();
  } else if (!strcmp(cmd, "SHOW")) {
    u8g2.sendBuffer(); ok();
  } else if (!strcmp(cmd, "FONT")) {
    char* a = nextToken();
    if (!a) { err(2, "usage: FONT <SMALL|MED|BIG>"); return; }
    upper(a);
    if      (!strcmp(a, "SMALL")) curFont = F_SMALL;
    else if (!strcmp(a, "MED"))   curFont = F_MED;
    else if (!strcmp(a, "BIG"))   curFont = F_BIG;
    else { err(4, "invalid font"); return; }
    ok();
  } else if (!strcmp(cmd, "TEXT")) {
    char* ln = nextToken();
    char* al = nextToken();
    if (!ln || !al) { err(2, "usage: TEXT <line> <L|C|R> <text>"); return; }
    char* text = restOfLine();
    u8g2.setFont(fontOf(curFont));
    int lh = u8g2.getMaxCharHeight();
    int y  = atoi(ln) * lh + u8g2.getAscent();
    int w  = u8g2.getUTF8Width(text);
    char a = toupper((unsigned char)al[0]);
    int x  = (a == 'C') ? (128 - w) / 2 : (a == 'R') ? (128 - w) : 0;
    if (x < 0) x = 0;
    u8g2.drawUTF8(x, y, text);
    ok();
  } else if (!strcmp(cmd, "PRINT")) {
    char* xs = nextToken();
    char* ys = nextToken();
    if (!xs || !ys) { err(2, "usage: PRINT <x> <y> <text>"); return; }
    char* text = restOfLine();
    u8g2.setFont(fontOf(curFont));
    u8g2.drawUTF8(atoi(xs), atoi(ys), text);
    ok();
  } else if (!strcmp(cmd, "IMG")) {
    char* n = nextToken();
    if (!n) { err(2, "usage: IMG <splash|warning|panic>"); return; }
    if (drawImageByName(n)) ok(); else err(5, "unknown image");
  } else if (!strcmp(cmd, "CONTRAST")) {
    char* v = nextToken();
    if (!v) { err(2, "usage: CONTRAST <0-255>"); return; }
    int c = atoi(v); c = c < 0 ? 0 : c > 255 ? 255 : c;
    u8g2.setContrast(c); ok();
  } else if (!strcmp(cmd, "LED")) {
    char* s = nextToken();
    if (!s) { err(2, "usage: LED <ON|OFF|BLINK [ms]>"); return; }
    upper(s);
    if (!strcmp(s, "ON"))        { ledBlink = false; ledState = true;  digitalWrite(LED_PIN, HIGH); ok(); }
    else if (!strcmp(s, "OFF"))  { ledBlink = false; ledState = false; digitalWrite(LED_PIN, LOW);  ok(); }
    else if (!strcmp(s, "BLINK")){ char* p = nextToken(); ledPeriod = p ? atoi(p) : 500;
                                   if (ledPeriod < 50) ledPeriod = 50;
                                   ledBlink = true; ledLastToggle = millis(); ok(); }
    else err(3, "invalid state");
  } else if (!strcmp(cmd, "BEEP")) {
    char* fs = nextToken();
    char* ms = nextToken();
    if (!fs || !ms) { err(2, "usage: BEEP <freq_hz> <ms>"); return; }
    int freq = atoi(fs), dur = atoi(ms);
    if (freq < 50 || freq > 20000) { err(3, "freq out of range (50-20000)"); return; }
    if (dur < 1) dur = 1;
    ledcWriteTone(BUZZER_CH, freq);
    buzzerOffAt = millis() + dur;
    ok();
  } else if (!strcmp(cmd, "BUZZER")) {
    char* s = nextToken();
    if (!s) { err(2, "usage: BUZZER <ON|OFF>"); return; }
    upper(s);
    if (!strcmp(s, "ON"))       { ledcWriteTone(BUZZER_CH, 2000); buzzerOffAt = 0; ok(); }
    else if (!strcmp(s, "OFF")) { ledcWrite(BUZZER_CH, 0);        buzzerOffAt = 0; ok(); }
    else err(3, "invalid state");
  } else if (!strcmp(cmd, "RESET")) {
    u8g2.clearBuffer(); u8g2.sendBuffer();
    ledBlink = false; ledState = false; digitalWrite(LED_PIN, LOW);
    ledcWrite(BUZZER_CH, 0); buzzerOffAt = 0;
    curFont = F_SMALL;
    ok();
  } else if (!strcmp(cmd, "PING")) {
    raw("PONG");
  } else if (!strcmp(cmd, "VERSION")) {
    okMsg("display-ctl " FW_VERSION);
  } else {
    err(1, "unknown command");
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  // buzzer: set up the LEDC channel once (do NOT use tone()/noTone(), which
  // de-initialize the channel and emit an ESP-IDF error log on the serial).
  ledcSetup(BUZZER_CH, 2000, 10);
  ledcAttachPin(BUZZER_PIN, BUZZER_CH);
  ledcWrite(BUZZER_CH, 0);

  // silence ESP-IDF logs so they never corrupt the serial protocol
  esp_log_level_set("*", ESP_LOG_NONE);

  delay(200);
  u8g2.setI2CAddress(OLED_ADDR << 1);
  u8g2.setBusClock(400000);
  u8g2.begin();
  u8g2.setContrast(0xFF);

  // startup screen: splash until the host sends the first command
  u8g2.clearBuffer();
  u8g2.drawXBM(0, 0, IMG_W, IMG_H, img_splash);
  u8g2.sendBuffer();

  Serial.println("# display-ctl " FW_VERSION " ready");
}

void loop() {
  // 1) read lines from serial and process them
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      lineBuf[lineLen] = 0;
      processLine();
      lineLen = 0;
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    } else {
      lineLen = 0;                 // overflow: drop the line
      Serial.println("ERR 6 line too long");
    }
  }

  // 2) turn the buzzer off when the BEEP duration expires
  if (buzzerOffAt && (int32_t)(millis() - buzzerOffAt) >= 0) {
    ledcWrite(BUZZER_CH, 0);
    buzzerOffAt = 0;
  }

  // 3) blink the LED in BLINK mode
  if (ledBlink && millis() - ledLastToggle >= ledPeriod) {
    ledLastToggle = millis();
    ledState = !ledState;
    digitalWrite(LED_PIN, ledState);
  }
}
