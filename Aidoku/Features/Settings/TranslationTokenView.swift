//
//  TranslationTokenView.swift
//  Aidoku
//

import SwiftUI

struct TranslationTokenView: View {
    @State private var token = TranslationKeychain.token() ?? ""

    var body: some View {
        List {
            SecureField(NSLocalizedString("TRANSLATION_TOKEN"), text: $token)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            Button(NSLocalizedString("SAVE")) {
                TranslationKeychain.setToken(token)
                NotificationCenter.default.post(name: Notification.Name("Translation.token"), object: nil)
            }
        }
    }
}
