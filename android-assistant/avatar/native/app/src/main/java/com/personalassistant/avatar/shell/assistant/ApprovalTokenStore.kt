package com.personalassistant.avatar.shell.assistant

import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Stores the user-provisioned approval credential encrypted by Android Keystore. */
class ApprovalTokenStore(private val preferences: SharedPreferences) {
    fun hasToken(): Boolean = load()?.isNotBlank() == true

    fun save(rawToken: String) {
        val token = rawToken.trim()
        require(token.length in MIN_TOKEN_LENGTH..MAX_TOKEN_LENGTH) {
            "Approval token must be between $MIN_TOKEN_LENGTH and $MAX_TOKEN_LENGTH characters"
        }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        preferences.edit()
            .putString(KEY_IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .putString(KEY_VALUE, Base64.encodeToString(encrypted, Base64.NO_WRAP))
            .apply()
    }

    fun load(): String? {
        val iv = preferences.getString(KEY_IV, null) ?: return null
        val encrypted = preferences.getString(KEY_VALUE, null) ?: return null
        return runCatching {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(
                Cipher.DECRYPT_MODE,
                secretKey(),
                GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)),
            )
            String(
                cipher.doFinal(Base64.decode(encrypted, Base64.NO_WRAP)),
                Charsets.UTF_8,
            )
        }.getOrElse {
            clear()
            null
        }
    }

    fun clear() {
        preferences.edit().remove(KEY_IV).remove(KEY_VALUE).apply()
    }

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE).run {
            init(
                KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .build(),
            )
            generateKey()
        }
    }

    private companion object {
        const val KEYSTORE = "AndroidKeyStore"
        const val KEY_ALIAS = "personal-assistant-approval-v1"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val KEY_IV = "approval_token_iv"
        const val KEY_VALUE = "approval_token_value"
        const val MIN_TOKEN_LENGTH = 32
        const val MAX_TOKEN_LENGTH = 512
    }
}
