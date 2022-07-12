from abc import ABC, abstractmethod


class IEncryptor(ABC):
    @abstractmethod
    def encrypt(self, plaintext: bytes) -> bytes:
        """
        Encrypts data and returns the ciphertext.

        :param plaintext: Data that will be encrypted
        :return: Ciphertext generated by encrypting the plaintext
        """

    @abstractmethod
    def decrypt(self, ciphertext: bytes) -> bytes:
        """
        Decrypts data and returns the plaintext.

        :param ciphertext: Ciphertext that will be decrypted
        :return: Plaintext generated by decrypting the ciphertext
        """
