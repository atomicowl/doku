package com.example.billing;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.ws.server.endpoint.annotation.Endpoint;
import org.springframework.ws.server.endpoint.annotation.PayloadRoot;
import org.springframework.ws.server.endpoint.annotation.RequestPayload;
import org.springframework.ws.server.endpoint.annotation.ResponsePayload;

@Endpoint
public class BillingEndpoint {

    private static final String NAMESPACE = "urn:billing";

    @Autowired
    private InvoiceRepository invoiceRepository;

    @Autowired
    private LegacyMainframeClient mainframeClient;

    @PayloadRoot(namespace = NAMESPACE, localPart = "GetInvoiceRequest")
    @ResponsePayload
    public GetInvoiceResponse getInvoice(@RequestPayload GetInvoiceRequest request) {
        Invoice invoice = invoiceRepository.findById(request.getInvoiceId());
        if (invoice == null) {
            invoice = mainframeClient.lookupLegacyInvoice(request.getInvoiceId());
        }
        return new GetInvoiceResponse(invoice);
    }
}
