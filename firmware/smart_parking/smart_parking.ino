/*
 * Smart Parking - Arduino edge node
 * ---------------------------------
 * - Reads 4x HC-SR04 ultrasonic sensors (one per parking spot)
 * - Opens a servo gate (SG90 on A0) for 3 s when a car is at the entry or exit sensor
 * - Shows "Cars parked / Free spots" on a 20x4 I2C LCD (address 0x27)
 * - Sends spot occupancy to the Raspberry Pi over USB serial, 9600 baud,
 *   every ~500 ms in the format:  P:1,0,1,0   (1 = occupied, 0 = free)
 *
 * Pins: spot trig/echo = 2/3, 4/5, 6/7, 8/9 | gate entry 10/11 | gate exit 12/13 | servo A0 | LCD SDA/SCL
 */

#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <Servo.h>

LiquidCrystal_I2C lcd(0x27, 20, 4);

const int trigPins[4] = {2, 4, 6, 8};
const int echoPins[4] = {3, 5, 7, 9};
bool parkingStatus[4] = {false, false, false, false};
const int threshold = 14;

const int gateEntryTrig = 10;
const int gateEntryEcho = 11;
const int gateExitTrig  = 12;
const int gateExitEcho  = 13;
const int gateThreshold = 10;

Servo gateServo;
int gateOpen = 90;
int gateClosed = 0;

bool entryTriggered = false;
bool exitTriggered = false;

int lastCarCount = -1;

void setup() {
  Serial.begin(9600);

  for (int i = 0; i < 4; i++) {
    pinMode(trigPins[i], OUTPUT);
    pinMode(echoPins[i], INPUT);
  }

  pinMode(gateEntryTrig, OUTPUT);
  pinMode(gateEntryEcho, INPUT);
  pinMode(gateExitTrig, OUTPUT);
  pinMode(gateExitEcho, INPUT);

  gateServo.attach(A0);
  gateServo.write(gateClosed);

  lcd.begin();
  lcd.backlight();
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Smart Parking");
  lcd.setCursor(0, 1);
  lcd.print("Init...");
  delay(1000);
}

long getDistance(int trigPin, int echoPin) {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);
  long duration = pulseIn(echoPin, HIGH, 30000);
  if (duration == 0) return 999;
  long distance = duration * 0.034 / 2;
  return distance;
}

void openGate() {
  gateServo.write(gateOpen);
  delay(3000);
  gateServo.write(gateClosed);
}

void updateLCD(int carCount) {
  if (carCount == lastCarCount) return;
  lastCarCount = carCount;
  int freeSpots = 4 - carCount;

  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Cars parked: ");
  lcd.print(carCount);
  lcd.setCursor(0, 1);
  lcd.print("Free spots: ");
  lcd.print(freeSpots);
}

void loop() {
  for (int i = 0; i < 4; i++) {
    long d = getDistance(trigPins[i], echoPins[i]);
    parkingStatus[i] = (d < threshold);
  }

  int carCount = 0;
  for (int i = 0; i < 4; i++) {
    if (parkingStatus[i]) carCount++;
  }
  updateLCD(carCount);

  long entryDist = getDistance(gateEntryTrig, gateEntryEcho);
  long exitDist  = getDistance(gateExitTrig, gateExitEcho);

  if (entryDist < gateThreshold && !entryTriggered) {
    openGate();
    entryTriggered = true;
  } else if (entryDist >= gateThreshold + 5) {
    entryTriggered = false;
  }

  if (exitDist < gateThreshold && !exitTriggered) {
    openGate();
    exitTriggered = true;
  } else if (exitDist >= gateThreshold + 5) {
    exitTriggered = false;
  }

  Serial.print("P:");
  for (int i = 0; i < 4; i++) {
    Serial.print(parkingStatus[i] ? 1 : 0);
    if (i < 3) Serial.print(",");
  }
  Serial.println();
  delay(500);
}