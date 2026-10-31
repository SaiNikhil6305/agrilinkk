// Firebase Modular SDK: Google sign-in, phone OTP, email verification and password reset.
// The Firebase ID token is exchanged at /api/auth/firebase-login; the role always comes from the backend DB.
import { initializeApp } from "firebase/app";
import {
  getAuth, GoogleAuthProvider, signInWithPopup, RecaptchaVerifier, signInWithPhoneNumber,
  createUserWithEmailAndPassword, signInWithEmailAndPassword, sendEmailVerification,
  sendPasswordResetEmail, signOut,
} from "firebase/auth";

const cfg = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

export const firebaseConfigured = Boolean(cfg.apiKey && cfg.projectId);
const auth = firebaseConfigured ? getAuth(initializeApp(cfg)) : null;

function need() { if (!auth) throw new Error("Firebase is not configured (VITE_FIREBASE_* variables)."); }

export async function googleSignIn() {
  need();
  const cred = await signInWithPopup(auth, new GoogleAuthProvider());
  return cred.user.getIdToken();
}

let confirmation = null;
export async function sendOtp(phone, containerId) {
  need();
  const verifier = new RecaptchaVerifier(auth, containerId, { size: "invisible" });
  confirmation = await signInWithPhoneNumber(auth, phone, verifier);
}
export async function verifyOtp(code) {
  if (!confirmation) throw new Error("Request an OTP first");
  const cred = await confirmation.confirm(code);
  return cred.user.getIdToken();
}

export async function emailSignUp(email, password) {
  need();
  const cred = await createUserWithEmailAndPassword(auth, email, password);
  await sendEmailVerification(cred.user);
  await signOut(auth);
}
export async function emailSignIn(email, password) {
  need();
  const cred = await signInWithEmailAndPassword(auth, email, password);
  if (!cred.user.emailVerified) {
    await sendEmailVerification(cred.user);
    await signOut(auth);
    throw new Error("Please verify your email first — we've sent the link again.");
  }
  return cred.user.getIdToken();
}
export async function resetPassword(email) { need(); await sendPasswordResetEmail(auth, email); }
export async function firebaseSignOut() { if (auth) await signOut(auth).catch(() => {}); }
